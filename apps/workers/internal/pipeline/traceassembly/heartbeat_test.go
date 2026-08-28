package traceassembly

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
)

// heartbeatOf runs one heartbeat over a pipeline holding the given batches and
// returns the line it wrote, decoded.
func heartbeatOf(t *testing.T, held []pendingCommit, now time.Time) map[string]any {
	t.Helper()

	var captured bytes.Buffer
	pipeline := Pipeline{
		stages: stagesFor(t, 100),
		log: slog.New(slog.NewJSONHandler(&captured,
			&slog.HandlerOptions{Level: slog.LevelInfo})),
		limits:       config.SpanIngest{Topic: "spans-raw"},
		retimedSpans: 4,
	}
	for _, entry := range held {
		pipeline.commits.hold(entry)
	}

	pipeline.heartbeat(context.Background(), now)

	if captured.Len() == 0 {
		t.Fatal("the heartbeat wrote nothing at INFO; a dead worker still looks like a live one")
	}
	var line map[string]any
	if err := json.Unmarshal(captured.Bytes(), &line); err != nil {
		t.Fatalf("the heartbeat is not one JSON object: %v (%q)", err, captured.String())
	}
	return line
}

// A worker's entire output at production level was one line at startup, which
// makes a healthy worker and a dead one identical in the logs. The heartbeat is
// what separates them, so it has to reach INFO and carry what is being held.
func TestTheHeartbeatReportsWhatThePipelineIsHolding(t *testing.T) {
	line := heartbeatOf(t, nil, invariantStart)

	if line["level"] != "INFO" {
		t.Errorf("level = %v, want INFO: DEBUG is off in production", line["level"])
	}
	for _, field := range []string{
		"buffered_traces", "buffered_spans", "uncommitted_records",
		"commit_lag_ms", "counter_series",
		"spans_kept", "spans_dropped", "traces_closed_early", "spans_retimed",
	} {
		if _, carried := line[field]; !carried {
			t.Errorf("the heartbeat does not carry %s", field)
		}
	}
	if line["spans_retimed"] != float64(4) {
		t.Errorf("spans_retimed = %v, want the running total (4)", line["spans_retimed"])
	}

	// The control plane writes one structured line per request with snake_case
	// fields and durations as integer `*_ms`. Matching it is what lets one
	// query read the data plane and the control plane together.
	for key := range line {
		if strings.ToLower(key) != key {
			t.Errorf("field %q is not snake_case; the control plane's fields are", key)
		}
	}
}

// Consumer lag otherwise takes a `docker exec` to read, and it is the number
// that shows a pipeline which has stopped settling.
func TestTheHeartbeatCarriesHowFarBehindCommitsAre(t *testing.T) {
	polledAt := invariantStart
	line := heartbeatOf(t, []pendingCommit{
		{positions: fakePositions(3), polledAt: polledAt},
		{positions: fakePositions(2), polledAt: polledAt.Add(30 * time.Second)},
	}, polledAt.Add(90*time.Second))

	// The oldest batch held, not the newest: that is what "behind" means.
	if line["commit_lag_ms"] != float64(90_000) {
		t.Errorf("commit_lag_ms = %v, want 90000", line["commit_lag_ms"])
	}
	if line["uncommitted_records"] != float64(5) {
		t.Errorf("uncommitted_records = %v, want 5", line["uncommitted_records"])
	}
}

// Nothing held is not lag, and a zero poll time is not a fifty-six-year-old
// batch.
func TestNothingHeldIsNoCommitLag(t *testing.T) {
	var queue commitQueue
	if lag := queue.commitLag(invariantStart); lag != 0 {
		t.Errorf("commitLag() = %s on an empty queue, want 0", lag)
	}

	queue.hold(pendingCommit{positions: fakePositions(1)})
	if lag := queue.commitLag(invariantStart); lag != 0 {
		t.Errorf("commitLag() = %s for an unstamped batch, want 0", lag)
	}
}
