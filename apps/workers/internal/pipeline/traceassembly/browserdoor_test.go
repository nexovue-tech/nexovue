package traceassembly

import (
	"testing"

	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/serviceedges"
)

// A site key is printed in its page, so anyone can send a SERVER span through
// the browser door. Such a span must count in none of the rollups — not the
// counters, `endpoint_stats_1m`, `service_stats_1m` nor `service_versions_1m`
// (F28 D55, AC17) — while it still reaches the edge rollup and the sampling
// buffer, so the trace view keeps it. The secret-key door's identical span
// still counts everywhere, so the filter cannot over-reach (browser-door T1.1,
// T1.2).
func TestABrowserDoorSpanCountsInNoRollupButIsStillKept(t *testing.T) {
	cases := map[nexovuev1.Door]bool{
		nexovuev1.Door_DOOR_RUM:  false,
		nexovuev1.Door_DOOR_OTLP: true,
	}
	for door, wantCounted := range cases {
		t.Run(door.String(), func(t *testing.T) {
			stages := stagesFor(t, 100)
			// A parent makes the span a callee the edge accumulator holds for its
			// caller, so "the edges saw it" is observable.
			message := marshalEnvelope(t, "1.27.0", spanOptions{
				kind:         tracepb.Span_SPAN_KIND_SERVER,
				parentSpanID: testParentID,
				door:         door,
			})

			result, err := stages.absorbMessage(invariantStart, message,
				handling{count: true, store: true})
			if err != nil {
				t.Fatalf("absorbMessage: %v", err)
			}

			if pending := stages.edges.Stats().PendingSpans; pending != 1 {
				t.Errorf("the edge accumulator holds %d spans, want 1: edges see every door", pending)
			}
			if kept := len(result.rows) + stages.buffer.Stats().BufferedSpans; kept != 1 {
				t.Errorf("the sampler holds or released %d spans, want 1: the trace view keeps "+
					"browser spans", kept)
			}

			drained := stages.counters.DrainAll()
			counted := map[string]int{
				"counter points":      len(drained.Points),
				"endpoint_stats_1m":   len(drained.Endpoints),
				"service_stats_1m":    len(drained.Services),
				"service_versions_1m": len(drained.Versions),
			}
			for output, rows := range counted {
				if got := rows > 0; got != wantCounted {
					t.Errorf("%s drained %d rows through %s, want counted = %v",
						output, rows, door, wantCounted)
				}
			}
			if counts := !result.latestMinute.IsZero(); counts != wantCounted {
				t.Errorf("latestMinute = %v, want a counter minute only when the span is counted",
					result.latestMinute)
			}
		})
	}
}

// The edge rollup takes a browser span, and its row says so: F3's rules read
// the server door alone (A63), so the envelope's door must reach the edge.
func TestABrowserDoorCallReachesTheEdgeRollupAsABrowserEdge(t *testing.T) {
	cases := map[nexovuev1.Door]string{
		nexovuev1.Door_DOOR_RUM:  serviceedges.DoorBrowser,
		nexovuev1.Door_DOOR_OTLP: serviceedges.DoorServer,
	}
	for door, want := range cases {
		t.Run(door.String(), func(t *testing.T) {
			stages := stagesFor(t, 100)
			message := marshalEnvelope(t, "1.27.0", spanOptions{
				kind: tracepb.Span_SPAN_KIND_CLIENT,
				door: door,
			})

			if _, err := stages.absorbMessage(invariantStart, message,
				handling{count: true, store: true}); err != nil {
				t.Fatalf("absorbMessage: %v", err)
			}

			edges := stages.edges.DrainAll()
			if len(edges) != 1 || edges[0].Door != want {
				t.Fatalf("expected one %q edge, got %+v", want, edges)
			}
		})
	}
}
