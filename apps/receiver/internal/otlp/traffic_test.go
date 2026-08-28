package otlp_test

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// summaryInterval is short enough to keep the suite quick and long enough that
// one tick does not race the assertions that follow it.
const summaryInterval = 20 * time.Millisecond

// recordedLines returns the JSON log lines a logger wrote.
func recordedLines(t *testing.T, written *bytes.Buffer) []map[string]any {
	t.Helper()
	var lines []map[string]any
	for _, raw := range strings.Split(strings.TrimSpace(written.String()), "\n") {
		if raw == "" {
			continue
		}
		var line map[string]any
		if err := json.Unmarshal([]byte(raw), &line); err != nil {
			t.Fatalf("log line is not JSON: %v (%s)", err, raw)
		}
		lines = append(lines, line)
	}
	return lines
}

func summaryLine(t *testing.T, written *bytes.Buffer) map[string]any {
	t.Helper()
	for _, line := range recordedLines(t, written) {
		if line["msg"] == "ingest summary" {
			return line
		}
	}
	t.Fatalf("no summary was written; the receiver would be invisible (%s)", written)
	return nil
}

// runSummary drives one window and returns what it logged.
func runSummary(t *testing.T, traffic *otlp.Traffic) *bytes.Buffer {
	t.Helper()
	written := &bytes.Buffer{}
	log := slog.New(slog.NewJSONHandler(written, nil))

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		traffic.LogSummaryEvery(ctx, log, summaryInterval)
	}()

	time.Sleep(summaryInterval * 3)
	cancel()
	<-done
	return written
}

func TestSummaryIsWrittenEvenWhenNothingArrives(t *testing.T) {
	// The whole point: a receiver serving nothing must not look like a receiver
	// serving everything, and both look like silence today.
	line := summaryLine(t, runSummary(t, otlp.NewTraffic()))

	if line["accepted_spans"] != float64(0) {
		t.Errorf("accepted_spans = %v, want 0 stated outright", line["accepted_spans"])
	}
	for _, field := range []string{"accepted_requests", "refused_requests", "refused_spans", "duration_ms"} {
		if _, present := line[field]; !present {
			t.Errorf("summary has no %s; a missing field cannot be queried for zero", field)
		}
	}
}

func TestSummaryReportsWhatTheWindowSaw(t *testing.T) {
	traffic := otlp.NewTraffic()
	traffic.Accepted(7)
	traffic.Accepted(3)
	traffic.Refused("throttled", 5)

	line := summaryLine(t, runSummary(t, traffic))

	if line["accepted_requests"] != float64(2) {
		t.Errorf("accepted_requests = %v, want 2", line["accepted_requests"])
	}
	if line["accepted_spans"] != float64(10) {
		t.Errorf("accepted_spans = %v, want 10: spans are the unit anyone cares about losing",
			line["accepted_spans"])
	}
	if line["refused_requests"] != float64(1) {
		t.Errorf("refused_requests = %v, want 1", line["refused_requests"])
	}
	if line["refused_throttled"] != float64(1) {
		t.Errorf("refused_throttled = %v, want 1: the summary has to say why", line["refused_throttled"])
	}
}

func TestSummaryCountsAreThisWindowOnly(t *testing.T) {
	traffic := otlp.NewTraffic()
	traffic.Accepted(4)

	lines := recordedLines(t, runSummary(t, traffic))

	counted := 0
	for _, line := range lines {
		if line["msg"] != "ingest summary" {
			continue
		}
		counted++
		if counted > 1 && line["accepted_spans"] != float64(0) {
			t.Errorf("a later window reported %v spans; totals bury the zero that matters",
				line["accepted_spans"])
		}
	}
	if counted < 2 {
		t.Fatalf("only %d summaries in %s; the heartbeat has to repeat", counted, summaryInterval*3)
	}
}

func TestRepeatedRefusalsAreCountedNotLogged(t *testing.T) {
	traffic := otlp.NewTraffic()

	if !traffic.Refused("unavailable", 1) {
		t.Fatal("the first refusal of a reason must be worth a line, or the outage is silent")
	}
	for range 100 {
		if traffic.Refused("unavailable", 1) {
			t.Fatal("every refusal wanted its own line; a broker outage would flood the log " +
				"it is diagnosed from")
		}
	}
}

// The control plane logs method, path, status and duration_ms per request. The
// door's own event lines use the same names, so one query spans both services.
func TestRefusalLineUsesTheSharedFieldNames(t *testing.T) {
	written := &bytes.Buffer{}
	d := newDoor(t, doorOptions{
		keys: unreachableKeys{},
		log:  slog.New(slog.NewJSONHandler(written, nil)),
	})

	post(t, d, encodedRequest(t, exportRequest(traceID(0x11))), nil)

	lines := recordedLines(t, written)
	if len(lines) == 0 {
		t.Fatal("an unreachable control plane produced no log line at all")
	}
	line := lines[len(lines)-1]

	if line["method"] != http.MethodPost {
		t.Errorf("method = %v, want %q", line["method"], http.MethodPost)
	}
	if line["path"] != otlp.TracesPath {
		t.Errorf("path = %v, want %q", line["path"], otlp.TracesPath)
	}
	if line["status"] != float64(http.StatusServiceUnavailable) {
		t.Errorf("status = %v, want %d", line["status"], http.StatusServiceUnavailable)
	}
	if _, present := line["duration_ms"]; !present {
		t.Error("no duration_ms; the control plane logs one and the query is shared")
	}
	if line["level"] != slog.LevelError.String() {
		t.Errorf("level = %v, want ERROR: an unreachable control plane is our fault", line["level"])
	}
}
