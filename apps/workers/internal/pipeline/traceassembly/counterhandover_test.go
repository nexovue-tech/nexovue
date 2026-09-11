package traceassembly

import (
	"fmt"
	"testing"

	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
)

// The counter half of the rebalance defect's root, asserted on the values that
// reach ClickHouse rather than on anything a double could set.
//
// A minute bucket sums across partitions, so a partition handed to another
// worker leaves its contribution behind in an open bucket. The bucket is
// flushed anyway, while the gaining worker re-reads the same uncommitted
// records and counts them into its own — and `metrics_sum` is summed on read,
// so the two rows add. Unlike a duplicated span row there is nothing to
// collapse afterwards: once 100 requests have been stored as 130 the individual
// contributions are gone.
func TestARevokedPartitionsSpanCountsLeaveTheBucket(t *testing.T) {
	pipeline := pipelineForHandover(t, newRecordingStore())

	absorbOn(t, pipeline, spansMoved, 100, 1)
	absorbOn(t, pipeline, spansKept, 100, 2)

	pipeline.release(map[string][]int32{pipeline.topic: {spansMoved}})

	counted := totalsByMetric(pipeline.stages.counters.DrainAll())
	if got := counted[derive.MetricSpanCount]; got != spansPerTrace {
		t.Errorf("%s drained %v, want %d — the trace on the partition this worker "+
			"still owns. The other trace's records are uncommitted, so the worker "+
			"that took the partition over counts them again",
			derive.MetricSpanCount, got, spansPerTrace)
	}
	if got := counted[derive.MetricSpanErrors]; got != spansPerTrace {
		t.Errorf("%s drained %v, want %d", derive.MetricSpanErrors, got, spansPerTrace)
	}
}

// The endpoint rollup is filled from the same Observe and drained in the same
// call, so it has the same defect and must lose the same partition's share.
// Its latency sample goes with it: a duration counted here and again by the new
// owner weights the stored quantile state twice.
func TestARevokedPartitionsEndpointRollupLeavesTheBucket(t *testing.T) {
	pipeline := pipelineForHandover(t, newRecordingStore())

	absorbOn(t, pipeline, spansMoved, 100, 1)
	absorbOn(t, pipeline, spansKept, 100, 2)

	pipeline.release(map[string][]int32{pipeline.topic: {spansMoved}})

	requests, errored, sampled := rollupTotals(pipeline.stages.counters.DrainAll())
	if requests != spansPerTrace {
		t.Errorf("endpoint_stats_1m drained %d requests, want %d", requests, spansPerTrace)
	}
	if errored != spansPerTrace {
		t.Errorf("endpoint_stats_1m drained %d errors, want %d", errored, spansPerTrace)
	}
	if sampled != spansPerTrace {
		t.Errorf("endpoint_stats_1m drained %d latency samples, want %d: a duration "+
			"the new owner samples too would weight the quantile state twice",
			sampled, spansPerTrace)
	}
}

// The service-edge rollup accumulates the same way and is summed on read the
// same way, so `service_edges_1m.call_count` is over-summed by a handover
// exactly as `metrics_sum` is.
func TestARevokedPartitionsEdgesLeaveTheBucket(t *testing.T) {
	pipeline := pipelineForHandover(t, newRecordingStore())

	absorbCallOn(t, pipeline, spansMoved, 200, 1)
	absorbCallOn(t, pipeline, spansKept, 200, 2)

	pipeline.release(map[string][]int32{pipeline.topic: {spansMoved}})

	var calls uint64
	var sampled int
	for _, edge := range pipeline.stages.edges.DrainAll() {
		calls += edge.CallCount
		sampled += len(edge.LatencyMS)
	}
	if calls != 1 {
		t.Errorf("service_edges_1m drained %d calls, want 1 — the call on the "+
			"partition this worker still owns", calls)
	}
	if sampled != 1 {
		t.Errorf("service_edges_1m drained %d latency samples, want 1", sampled)
	}
}

// A revoke that names no partition of this topic must leave every counter
// alone. The drop is a handover, not a flush.
func TestCountersSurviveARevokeThatNamesAnotherTopic(t *testing.T) {
	pipeline := pipelineForHandover(t, newRecordingStore())

	absorbOn(t, pipeline, spansKept, 100, 1)
	absorbCallOn(t, pipeline, spansKept, 200, 2)

	pipeline.release(map[string][]int32{"logs-raw": {spansKept}})
	pipeline.release(map[string][]int32{pipeline.topic: {}})

	counted := totalsByMetric(pipeline.stages.counters.DrainAll())
	// Two messages on the kept partition: the three-span trace and the two-span
	// call.
	const wantSpans = spansPerTrace + 2
	if got := counted[derive.MetricSpanCount]; got != wantSpans {
		t.Errorf("%s drained %v, want %d: another topic's rebalance and an empty "+
			"set must both leave this pipeline's counters alone",
			derive.MetricSpanCount, got, wantSpans)
	}
	if edges := pipeline.stages.edges.DrainAll(); len(edges) != 1 {
		t.Errorf("drained %d edges, want 1", len(edges))
	}
}

// absorbCallOn runs one two-span call — a CLIENT span answered by the SERVER
// span it is the parent of — through the stages as a record on the given
// partition. It is the shape the edge accumulator pairs; the SERVER-only traces
// the other helpers build take no part in the graph.
func absorbCallOn(t *testing.T, pipeline *Pipeline, partition int32, offset int64, index int) {
	t.Helper()
	if !pipeline.seen.FirstDelivery(pipeline.topic, partition, offset) {
		t.Fatalf("offset %d on partition %d was refused on its first delivery", offset, partition)
	}
	if _, err := pipeline.stages.absorbMessage(invariantStart, callMessage(t, index),
		handling{count: true, store: true, partition: partition}); err != nil {
		t.Fatalf("absorbMessage: %v", err)
	}
}

// callMessage builds the envelope for one trace holding a single call: a
// checkout CLIENT span and the payments SERVER span that answered it.
func callMessage(t *testing.T, index int) []byte {
	t.Helper()

	traceID := make([]byte, 16)
	copy(traceID, fmt.Sprintf("call--%09d", index))
	callerID := make([]byte, 8)
	copy(callerID, fmt.Sprintf("c-%05d", index))
	calleeID := make([]byte, 8)
	copy(calleeID, fmt.Sprintf("s-%05d", index))

	start := uint64(invariantStart.UnixNano())
	end := start + uint64(spanDuration)
	ok := &tracepb.Status{Code: tracepb.Status_STATUS_CODE_OK}

	caller := &tracepb.ResourceSpans{
		Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
			attribute(attrServiceName, stringValue("checkout")),
		}},
		ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{{
			TraceId:           traceID,
			SpanId:            callerID,
			Name:              "POST /charge",
			Kind:              tracepb.Span_SPAN_KIND_CLIENT,
			StartTimeUnixNano: start,
			EndTimeUnixNano:   end,
			Status:            ok,
		}}}},
	}
	callee := &tracepb.ResourceSpans{
		Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
			attribute(attrServiceName, stringValue("payments")),
		}},
		ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{{
			TraceId:           traceID,
			SpanId:            calleeID,
			ParentSpanId:      callerID,
			Name:              "POST /charge",
			Kind:              tracepb.Span_SPAN_KIND_SERVER,
			StartTimeUnixNano: start,
			EndTimeUnixNano:   end,
			Status:            ok,
		}}}},
	}

	payload, err := proto.Marshal(&tracesvc.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{caller, callee},
	})
	if err != nil {
		t.Fatalf("marshalling the OTLP request: %v", err)
	}
	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:  "tenant-a",
		ProjectId: "project-a",
		Door:      nexovuev1.Door_DOOR_OTLP,
		Signal:    nexovuev1.Signal_SIGNAL_SPANS,
		Payload:   payload,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	return message
}
