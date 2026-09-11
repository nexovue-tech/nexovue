package traceassembly

import (
	"testing"
	"time"

	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
)

// skewedMessage is one span a client stamped at startedAt and we took at
// receivedAt. The two are separate arguments because the gap between them is
// the whole subject of these tests.
func skewedMessage(t *testing.T, startedAt, receivedAt time.Time) []byte {
	t.Helper()

	start := uint64(startedAt.UnixNano())
	payload, err := proto.Marshal(&tracesvc.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{{
			Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
				attribute(attrServiceName, stringValue("checkout")),
			}},
			ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{{
				TraceId:           []byte("skewed-trace-01"),
				SpanId:            []byte("skewed1"),
				Name:              "GET /checkout",
				StartTimeUnixNano: start,
				EndTimeUnixNano:   start + uint64(5*time.Millisecond),
				Status:            &tracepb.Status{Code: tracepb.Status_STATUS_CODE_OK},
			}}}},
		}},
	})
	if err != nil {
		t.Fatalf("marshalling the OTLP request: %v", err)
	}

	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:           "tenant-a",
		ProjectId:          "project-a",
		Door:               nexovuev1.Door_DOOR_OTLP,
		Signal:             nexovuev1.Signal_SIGNAL_SPANS,
		ReceivedAtUnixNano: receivedAt.UnixNano(),
		Payload:            payload,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	return message
}

// The wedge this guards against: a batch's offsets wait for the minute bucket
// its counters fed, so a bucket a client's clock put in the future never
// settles and the offsets never move. Nothing turns red — the spans still reach
// storage, the message decoded perfectly so it never reaches the dead-letter
// topic, and the pipeline still reports Running — while the held-commit list
// and the counter map grow until the worker is OOM-killed and re-reads the same
// span.
func TestAFutureDatedSpanDoesNotWedgeOffsetCommits(t *testing.T) {
	// A VM whose clock is a year out is the ordinary form of this: no NTP.
	const clientClockAhead = 365 * 24 * time.Hour

	stages := stagesFor(t, 100)
	receivedAt := invariantStart

	absorbed, err := stages.absorbMessage(
		receivedAt, skewedMessage(t, receivedAt.Add(clientClockAhead), receivedAt), handling{count: true, store: true})
	if err != nil {
		t.Fatalf("absorbMessage() = %v, want no error", err)
	}

	pipeline := Pipeline{stages: stages}
	var queue consume.Queue[spanStorage]
	queue.Hold(consume.Batch[spanStorage]{
		Positions: fakePositions(1),
		PolledAt:  receivedAt,
		Produced: spanStorage{
			spansStoredAt: receivedAt.Add(10 * time.Second),
			latestMinute:  absorbed.latestMinute,
		},
	})

	// An hour later: long past every deadline this batch could legitimately be
	// waiting on — the sampling window, the minute, and its grace.
	settled := receivedAt.Add(time.Hour)

	if due := queue.Ready(storedAt(&pipeline, settled)); len(due) != 1 {
		t.Errorf("committed %d records an hour after the batch, want 1: one future-dated "+
			"span holds every offset this worker owns, for good", len(due))
	}
	if points := stages.counters.Drain(settled).Points; len(points) == 0 {
		t.Error("the counter bucket never drained; it is held forever and the map only grows")
	}
	if held := stages.counters.Pending(); held != 0 {
		t.Errorf("Pending() = %d an hour later, want 0", held)
	}
}

// Discarding the span would cost a count, and counts are exact by invariant. It
// is counted at the moment we received it instead — our own clock is the only
// one in the path we control.
func TestAnUntrustedSpanTimestampIsCountedAtReceiveTime(t *testing.T) {
	stages := stagesFor(t, 100)
	receivedAt := time.Date(2026, 8, 27, 10, 0, 30, 0, time.UTC)

	for _, clientClock := range map[string]time.Time{
		"a year ahead":  receivedAt.AddDate(1, 0, 0),
		"a year behind": receivedAt.AddDate(-1, 0, 0),
		"the epoch":     time.Unix(0, 0).UTC(),
	} {
		if _, err := stages.absorbMessage(
			receivedAt, skewedMessage(t, clientClock, receivedAt), handling{count: true, store: true}); err != nil {
			t.Fatalf("absorbMessage() = %v, want no error", err)
		}
	}

	points := stages.counters.DrainAll()
	counted := totalsByMetric(points)
	if counted[derive.MetricSpanCount] != 3 {
		t.Errorf("%s = %v, want 3: no span may be dropped for its clock",
			derive.MetricSpanCount, counted[derive.MetricSpanCount])
	}

	receiveMinute := receivedAt.Truncate(time.Minute)
	for _, point := range points.Points {
		if !point.Timestamp.Equal(receiveMinute) {
			t.Errorf("counted %s at %s, want the minute we received it (%s)",
				point.MetricName, point.Timestamp, receiveMinute)
		}
	}
}

// The retiming has to be visible, or a customer's broken clock silently moves
// their traffic to a different minute and nothing ever says so.
func TestRetimingASpanIsReported(t *testing.T) {
	stages := stagesFor(t, 100)
	receivedAt := invariantStart

	trusted, err := stages.absorbMessage(
		receivedAt, skewedMessage(t, receivedAt.Add(-time.Second), receivedAt), handling{count: true, store: true})
	if err != nil {
		t.Fatalf("absorbMessage() = %v, want no error", err)
	}
	if trusted.skew.spans != 0 {
		t.Error("a span one second old was retimed; ordinary skew must be left alone")
	}

	skewed, err := stages.absorbMessage(
		receivedAt, skewedMessage(t, receivedAt.AddDate(1, 0, 0), receivedAt), handling{count: true, store: true})
	if err != nil {
		t.Fatalf("absorbMessage() = %v, want no error", err)
	}
	if skewed.skew.spans != 1 {
		t.Errorf("skew.spans = %d, want 1", skewed.skew.spans)
	}
	if skewed.skew.tenantID != "tenant-a" || skewed.skew.projectID != "project-a" {
		t.Errorf("skew names %s/%s, want the tenant whose clock is wrong",
			skewed.skew.tenantID, skewed.skew.projectID)
	}
	if skewed.skew.worst < 300*24*time.Hour {
		t.Errorf("skew.worst = %s, want roughly the year the clock is out", skewed.skew.worst)
	}
}
