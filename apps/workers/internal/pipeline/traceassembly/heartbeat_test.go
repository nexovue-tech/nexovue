package traceassembly

import (
	"log/slog"
	"strings"
	"testing"
	"time"
)

// The heartbeat itself belongs to the consume loop; what trace assembly adds to
// it is this. Without these numbers a worker whose sampling buffer or counter
// map is only growing looks exactly like a healthy one.
func TestTheHeartbeatCarriesWhatTheTraceStagesHold(t *testing.T) {
	pipeline := Pipeline{
		stages:       stagesFor(t, 100),
		summaries:    newSummaries(time.Minute, 100),
		retimedSpans: 4,
	}

	held := make(map[string]slog.Value)
	for _, attr := range pipeline.Holding() {
		held[attr.Key] = attr.Value
	}

	for _, field := range []string{
		"buffered_traces", "buffered_spans", "counter_series",
		"spans_kept", "spans_dropped", "traces_closed_early", "spans_retimed",
		"summarised_traces", "trace_summaries_written", "trace_summaries_revised",
		"trace_summaries_evicted", "trace_summaries_truncated",
	} {
		if _, carried := held[field]; !carried {
			t.Errorf("the heartbeat does not carry %s", field)
		}
	}
	if got := held["spans_retimed"].Uint64(); got != 4 {
		t.Errorf("spans_retimed = %v, want the running total (4)", got)
	}

	// The control plane writes one structured line per request with snake_case
	// fields and durations as integer `*_ms`. Matching it is what lets one
	// query read the data plane and the control plane together.
	for key := range held {
		if strings.ToLower(key) != key {
			t.Errorf("field %q is not snake_case; the control plane's fields are", key)
		}
	}
}
