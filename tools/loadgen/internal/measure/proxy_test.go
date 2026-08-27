package measure

import (
	"testing"
	"time"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

func TestInspectCountsSpansAcrossResourcesAndScopes(t *testing.T) {
	first := []byte("0123456789abcdef")
	second := []byte("fedcba9876543210")

	body := marshalExport(t, &collectorpb.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{
			{ScopeSpans: []*tracepb.ScopeSpans{
				{Spans: []*tracepb.Span{{TraceId: first}, {TraceId: first}}},
				{Spans: []*tracepb.Span{{TraceId: second}}},
			}},
			{ScopeSpans: []*tracepb.ScopeSpans{
				{Spans: []*tracepb.Span{{TraceId: second}}},
			}},
		},
	})

	got := inspect(body)
	if got.spans != 4 {
		t.Errorf("spans = %d, want 4", got.spans)
	}
	// Lower-case hex is how the receiver keys Kafka and how ClickHouse stores
	// the column the lag query matches on.
	if want := "30313233343536373839616263646566"; got.traceID != want {
		t.Errorf("traceID = %q, want %q", got.traceID, want)
	}
}

// A body we cannot read is still forwarded, because the receiver's answer to it
// is a result worth recording. It simply contributes no spans.
func TestInspectTreatsAnUndecodableBodyAsNoSpans(t *testing.T) {
	got := inspect([]byte("not a protobuf export"))
	if got.spans != 0 || got.traceID != "" {
		t.Errorf("inspect(garbage) = %+v, want an empty export", got)
	}
}

func TestSampleClockSpacesSamplesByInterval(t *testing.T) {
	clock := newSampleClock()
	base := time.Unix(0, 0)
	interval := time.Second

	if !clock.due(base, interval) {
		t.Fatal("the first sample should always be taken")
	}
	if clock.due(base.Add(999*time.Millisecond), interval) {
		t.Error("a sample inside the interval should be skipped")
	}
	if !clock.due(base.Add(time.Second), interval) {
		t.Error("a sample at the interval should be taken")
	}
}

func TestHopByHopHeadersAreNotForwarded(t *testing.T) {
	source := map[string][]string{
		"X-Nexovue-Key":     {"nvk_live_test"},
		"Content-Type":      {"application/x-protobuf"},
		"Connection":        {"keep-alive"},
		"Transfer-Encoding": {"chunked"},
	}
	destination := map[string][]string{}
	copyHeaders(destination, source)

	if _, forwarded := destination["Connection"]; forwarded {
		t.Error("Connection is hop-by-hop and must not be forwarded")
	}
	// The ingest key must survive: the auth gate is inside the measured path.
	if got := destination["X-Nexovue-Key"]; len(got) != 1 || got[0] != "nvk_live_test" {
		t.Errorf("X-Nexovue-Key = %v, want it forwarded unchanged", got)
	}
}

func marshalExport(t *testing.T, request *collectorpb.ExportTraceServiceRequest) []byte {
	t.Helper()
	body, err := proto.Marshal(request)
	if err != nil {
		t.Fatalf("marshalling test export: %v", err)
	}
	return body
}
