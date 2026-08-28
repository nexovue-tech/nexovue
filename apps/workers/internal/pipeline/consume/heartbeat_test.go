package consume

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"
	"time"
)

var started = time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)

// stubStages is a pipeline's signal half reduced to what the loop reads from
// it. Every method the loop calls has to exist; only Holding says anything.
type stubStages struct{ holding []slog.Attr }

func (s stubStages) Absorb(context.Context, Polled) (bool, error) { return true, nil }

func (s stubStages) Settle(context.Context, time.Time) error { return nil }

func (s stubStages) Stored(time.Time, bool) bool { return true }

func (s stubStages) Drain(context.Context, time.Time) error { return nil }

func (s stubStages) Holding() []slog.Attr { return s.holding }

func (s stubStages) Describe() []slog.Attr { return nil }

// batchPolledAt is one held batch of `records` records, taken off the broker at
// polledAt.
func batchPolledAt(records int, polledAt time.Time) Batch[bool] {
	positions := make([]Position, 0, records)
	for offset := range records {
		positions = append(positions, Position{
			Topic: spansTopic, Partition: 0, Offset: int64(offset),
		})
	}
	return Batch[bool]{Positions: positions, PolledAt: polledAt}
}

// heartbeatOf runs one heartbeat over a loop holding the given batches and
// returns the line it wrote, decoded.
func heartbeatOf(t *testing.T, holding []Batch[bool], now time.Time) map[string]any {
	t.Helper()

	var captured bytes.Buffer
	loop := &Loop[bool]{
		settings: Settings{Name: "trace-assembly", Topic: spansTopic},
		log: slog.New(slog.NewJSONHandler(&captured,
			&slog.HandlerOptions{Level: slog.LevelInfo})),
		now: func() time.Time { return now },
	}
	for _, entry := range holding {
		loop.queue.Hold(entry)
	}

	loop.heartbeat(context.Background(), now,
		stubStages{holding: []slog.Attr{slog.Int("buffered_spans", 7)}})

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
// what separates them, so it has to reach INFO and carry what is being held —
// both the loop's own numbers and whatever the signal's stages contribute.
func TestTheHeartbeatReportsWhatThePipelineIsHolding(t *testing.T) {
	line := heartbeatOf(t, nil, started)

	if line["level"] != "INFO" {
		t.Errorf("level = %v, want INFO: DEBUG is off in production", line["level"])
	}
	for _, field := range []string{
		"topic", "uncommitted_records", "commit_lag_ms", "buffered_spans",
	} {
		if _, carried := line[field]; !carried {
			t.Errorf("the heartbeat does not carry %s", field)
		}
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
	line := heartbeatOf(t, []Batch[bool]{
		batchPolledAt(3, started),
		batchPolledAt(2, started.Add(30*time.Second)),
	}, started.Add(90*time.Second))

	// The oldest batch held, not the newest: that is what "behind" means.
	if line["commit_lag_ms"] != float64(90_000) {
		t.Errorf("commit_lag_ms = %v, want 90000", line["commit_lag_ms"])
	}
	if line["uncommitted_records"] != float64(5) {
		t.Errorf("uncommitted_records = %v, want 5", line["uncommitted_records"])
	}
}
