package derive_test

import (
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
)

// The partitions a series was counted on are provenance, not a dimension: they
// are kept apart in memory so a handover can drop one, and summed back together
// before anything is written. A row per partition would multiply every counter
// in `metrics_sum` by the topic's partition count.
func TestPartitionSharesOfOneSeriesDrainAsOneRow(t *testing.T) {
	counters := derive.NewCounters(0)
	for range 4 {
		counters.Observe(now, spanOn(1, base, "checkout", "/checkout", 10, false))
	}
	for range 6 {
		counters.Observe(now, spanOn(2, base, "checkout", "/checkout", 10, false))
	}

	drained := counters.Drain(base.Add(2 * time.Minute))
	if len(drained.Points) != 3 {
		t.Fatalf("drained %d points, want the three metrics of one series: %+v",
			len(drained.Points), drained.Points)
	}
	if got := valuesByMetric(drained)[derive.MetricSpanCount]; got != 10 {
		t.Errorf("%s = %v, want 10", derive.MetricSpanCount, got)
	}
	if len(drained.Endpoints) != 1 {
		t.Fatalf("drained %d endpoint rows, want one", len(drained.Endpoints))
	}
	if drained.Endpoints[0].RequestCount != 10 {
		t.Errorf("endpoint requests = %d, want 10", drained.Endpoints[0].RequestCount)
	}
	if got := len(drained.Endpoints[0].LatencyMS); got != 10 {
		t.Errorf("endpoint latency sample held %d durations, want 10: the shares' "+
			"samples merge rather than one of them winning", got)
	}
}

// The fix itself, at the value that reaches ClickHouse. A partition the group
// took back has counted nothing here, because the worker that took it over
// counts those same uncommitted records.
func TestARevokedPartitionsShareLeavesTheCounters(t *testing.T) {
	counters := derive.NewCounters(0)
	for range 4 {
		counters.Observe(now, spanOn(1, base, "checkout", "/checkout", 10, true))
	}
	for range 6 {
		counters.Observe(now, spanOn(2, base, "checkout", "/checkout", 10, true))
	}

	dropped := counters.Disown([]int32{2})
	if dropped.Spans != 6 {
		t.Errorf("Disown reported %d spans, want 6", dropped.Spans)
	}

	drained := counters.Drain(base.Add(2 * time.Minute))
	totals := valuesByMetric(drained)
	if totals[derive.MetricSpanCount] != 4 {
		t.Errorf("%s = %v, want 4 — only the partition still owned",
			derive.MetricSpanCount, totals[derive.MetricSpanCount])
	}
	if totals[derive.MetricSpanErrors] != 4 {
		t.Errorf("%s = %v, want 4", derive.MetricSpanErrors, totals[derive.MetricSpanErrors])
	}
	if totals[derive.MetricSpanDurationSum] != 40 {
		t.Errorf("%s = %v, want 40", derive.MetricSpanDurationSum, totals[derive.MetricSpanDurationSum])
	}
	if len(drained.Endpoints) != 1 || drained.Endpoints[0].RequestCount != 4 {
		t.Errorf("endpoint rollup drained %+v, want one row of 4 requests", drained.Endpoints)
	}
}

// A partition this worker never counted on must cost nothing.
func TestDisowningAnUncountedPartitionChangesNothing(t *testing.T) {
	counters := derive.NewCounters(0)
	counters.Observe(now, spanOn(1, base, "checkout", "/checkout", 10, false))

	if dropped := counters.Disown([]int32{7}); dropped != (derive.Disowned{}) {
		t.Errorf("Disown reported %+v, want nothing dropped", dropped)
	}
	if dropped := counters.Disown(nil); dropped != (derive.Disowned{}) {
		t.Errorf("Disown(nil) reported %+v, want nothing dropped", dropped)
	}
	if got := valuesByMetric(counters.Drain(base.Add(2 * time.Minute)))[derive.MetricSpanCount]; got != 1 {
		t.Errorf("%s = %v, want 1", derive.MetricSpanCount, got)
	}
}

// The block ClickHouse receives must be byte-identical when the same offsets
// are re-derived, or its insert deduplication cannot recognise the retry. Shares
// are folded in partition order for exactly that reason: a floating-point sum
// depends on the order it is taken in, and the order records interleave across
// partitions does not survive a replay.
func TestTheFoldDoesNotDependOnTheOrderPartitionsInterleave(t *testing.T) {
	forwards, backwards := derive.NewCounters(0), derive.NewCounters(0)
	durations := []float64{0.1, 0.2, 0.3, 0.4}

	for i, ms := range durations {
		forwards.Observe(now, spanOn(int32(i%2), base, "checkout", "/checkout", ms, false))
	}
	for i := len(durations) - 1; i >= 0; i-- {
		backwards.Observe(now, spanOn(int32(i%2), base, "checkout", "/checkout", durations[i], false))
	}

	first := valuesByMetric(forwards.Drain(base.Add(2 * time.Minute)))
	second := valuesByMetric(backwards.Drain(base.Add(2 * time.Minute)))
	if first[derive.MetricSpanDurationSum] != second[derive.MetricSpanDurationSum] {
		t.Errorf("duration sums differ by arrival order: %v vs %v",
			first[derive.MetricSpanDurationSum], second[derive.MetricSpanDurationSum])
	}
}

// spanOn is span with the Kafka partition its record arrived on.
func spanOn(
	partition int32,
	at time.Time,
	service, endpoint string,
	durationMS float64,
	errored bool,
) derive.Span {
	built := span(at, service, endpoint, durationMS, errored)
	built.Partition = partition
	// SERVER, so the endpoint rollup sees it too: both outputs come out of one
	// Observe and must lose a partition together.
	built.SpanKind = derive.KindServer
	built.SpanName = endpoint
	built.TraceID = []byte{0xaa, byte(partition)}
	built.SpanID = []byte{byte(partition), byte(len(endpoint)), byte(durationMS * 10)}
	return built
}
