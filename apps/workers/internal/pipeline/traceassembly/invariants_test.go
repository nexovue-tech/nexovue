package traceassembly

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
)

// spansPerTrace is what the demo application produces: a storefront span, a
// checkout span and a payments span.
const spansPerTrace = 3

var invariantStart = time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)

// stagesFor builds the per-record stages under one sampling percentage, which
// is the only setting these tests vary.
func stagesFor(t *testing.T, healthyPercent int) *stages {
	t.Helper()
	cfg := config.Config{
		Sampling: config.Sampling{
			Window:         10 * time.Second,
			DecisionTTL:    time.Minute,
			SlowThreshold:  2 * time.Second,
			HealthyPercent: healthyPercent,
			MaxTraces:      100_000,
			MaxSpans:       1_000_000,
		},
		Metrics: config.Metrics{LateGrace: 0},
	}
	built, err := newStages(cfg, Options{})
	if err != nil {
		t.Fatalf("newStages() = %v, want no error", err)
	}
	return built
}

// spanDuration is what the demo application's spans take. Well under the
// sampler's slow threshold, so a trace built from them is healthy unless it is
// deliberately failed.
const spanDuration = 5 * time.Millisecond

// traceMessage builds the envelope the receiver publishes for one trace: every
// span of a trace in one message, keyed by trace id.
func traceMessage(t *testing.T, index int, errored bool, attributes ...*commonpb.KeyValue) []byte {
	t.Helper()
	return traceMessageLasting(t, index, errored, spanDuration, attributes...)
}

// traceMessageLasting is the same trace with its spans given a duration, which
// is what decides whether the sampler calls the trace slow.
func traceMessageLasting(
	t *testing.T,
	index int,
	errored bool,
	duration time.Duration,
	attributes ...*commonpb.KeyValue,
) []byte {
	t.Helper()

	traceID := make([]byte, 16)
	copy(traceID, fmt.Sprintf("trace-%09d", index))

	status := &tracepb.Status{Code: tracepb.Status_STATUS_CODE_OK}
	if errored {
		status = &tracepb.Status{Code: tracepb.Status_STATUS_CODE_ERROR}
	}

	spans := make([]*tracepb.Span, 0, spansPerTrace)
	for position := range spansPerTrace {
		spanID := make([]byte, 8)
		copy(spanID, fmt.Sprintf("s%d-%05d", position, index))
		start := uint64(invariantStart.UnixNano())
		spans = append(spans, &tracepb.Span{
			TraceId: traceID,
			SpanId:  spanID,
			Name:    "GET /checkout",
			// SERVER, because each of the three services answered a call. It is
			// the kind the endpoint rollup counts, and the demo's own shape.
			Kind:              tracepb.Span_SPAN_KIND_SERVER,
			StartTimeUnixNano: start,
			EndTimeUnixNano:   start + uint64(duration),
			Attributes:        attributes,
			Status:            status,
		})
	}

	payload, err := proto.Marshal(&tracesvc.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{{
			Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
				attribute(attrServiceName, stringValue("checkout")),
			}},
			ScopeSpans: []*tracepb.ScopeSpans{{Spans: spans}},
		}},
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

func totalsByMetric(drained derive.Derived) map[string]float64 {
	totals := make(map[string]float64)
	for _, point := range drained.Points {
		totals[point.MetricName] += point.Value
	}
	return totals
}

// rollupTotals adds up the endpoint rollup a drain produced, which is what the
// Services screen sums when it reads `endpoint_stats_1m`.
func rollupTotals(drained derive.Derived) (requests, errors uint64, sampled int) {
	for _, row := range drained.Endpoints {
		requests += row.RequestCount
		errors += row.ErrorCount
		sampled += len(row.LatencyMS)
	}
	return requests, errors, sampled
}

// THE invariant of ADR 0010. Storage keeps a tenth of the healthy traces; the
// counters keep all of them. If this test ever fails, every dashboard and every
// anomaly detector downstream is lying by roughly a factor of ten.
func TestCountersStayExactWhileStorageIsSampledAtTenPercent(t *testing.T) {
	const traces = 2000
	const errorEvery = 20

	stages := stagesFor(t, 10)

	stored := 0
	errored := 0
	for index := range traces {
		isError := index%errorEvery == 0
		if isError {
			errored++
		}
		result, err := stages.absorbMessage(invariantStart, traceMessage(t, index, isError), true)
		if err != nil {
			t.Fatalf("absorbMessage() = %v, want no error", err)
		}
		stored += len(result.rows)
	}
	stored += len(stages.buffer.Expire(invariantStart.Add(time.Minute)))

	drained := stages.counters.DrainAll()
	counted := totalsByMetric(drained)
	wantSpans := float64(traces * spansPerTrace)
	wantErrors := float64(errored * spansPerTrace)

	// The endpoint rollup the Services screen reads is derived here too, so it
	// has to be exact by the same argument.
	rollupRequests, rollupErrors, _ := rollupTotals(drained)
	if float64(rollupRequests) != wantSpans || float64(rollupErrors) != wantErrors {
		t.Errorf("the endpoint rollup counted %d requests and %d errors, want %v and %v: "+
			"the Services screen must not read a sampled number",
			rollupRequests, rollupErrors, wantSpans, wantErrors)
	}

	if counted[derive.MetricSpanCount] != wantSpans {
		t.Errorf("%s = %v, want %v: the counters must see every span, sampled or not",
			derive.MetricSpanCount, counted[derive.MetricSpanCount], wantSpans)
	}
	if counted[derive.MetricSpanErrors] != wantErrors {
		t.Errorf("%s = %v, want %v", derive.MetricSpanErrors, counted[derive.MetricSpanErrors], wantErrors)
	}

	// And the sampler really did drop most of them, or the assertion above
	// would pass for a pipeline that is not sampling at all.
	if float64(stored) >= wantSpans*0.5 {
		t.Fatalf("storage kept %d of %v spans; the sampler is not sampling, so the "+
			"exactness above proves nothing", stored, wantSpans)
	}
	// Every error trace is kept whole on top of a tenth of the healthy ones.
	if stored < errored*spansPerTrace {
		t.Errorf("storage kept %d spans, fewer than the %d spans of the failed traces",
			stored, errored*spansPerTrace)
	}
}

// The counters must not move when the sampling rate does. Same traffic, two
// rates, identical totals.
func TestTheSamplingRateDoesNotChangeTheCounters(t *testing.T) {
	const traces = 500

	countedAt := func(percent int) derive.Derived {
		stages := stagesFor(t, percent)
		for index := range traces {
			if _, err := stages.absorbMessage(
				invariantStart, traceMessage(t, index, index%20 == 0), true); err != nil {
				t.Fatalf("absorbMessage() = %v, want no error", err)
			}
		}
		stages.buffer.Expire(invariantStart.Add(time.Minute))
		return stages.counters.DrainAll()
	}

	sampledDrain, everythingDrain := countedAt(10), countedAt(100)
	sampled, everything := totalsByMetric(sampledDrain), totalsByMetric(everythingDrain)
	for _, metric := range []string{
		derive.MetricSpanCount, derive.MetricSpanErrors, derive.MetricSpanDurationSum,
	} {
		if sampled[metric] != everything[metric] {
			t.Errorf("%s = %v at 10%% and %v at 100%%, want them equal",
				metric, sampled[metric], everything[metric])
		}
	}

	sampledRequests, sampledErrors, _ := rollupTotals(sampledDrain)
	allRequests, allErrors, _ := rollupTotals(everythingDrain)
	if sampledRequests != allRequests || sampledErrors != allErrors {
		t.Errorf("the endpoint rollup read %d/%d at 10%% and %d/%d at 100%%, want them equal",
			sampledRequests, sampledErrors, allRequests, allErrors)
	}
}

// The defect this replaced: `endpoint_stats_1m` was filled by a materialized
// view over `spans`, which is downstream of the sampler. Exporting 30 healthy,
// 30 slow and 30 failed traces put 5 / 30 / 30 of them in the rollup, so a
// service with a true 1% error rate displayed about 9% at a tenth of its real
// request rate. Derived here, all ninety are counted.
func TestTheEndpointRollupCountsSampledAwayTracesTheScreenWouldNeverSee(t *testing.T) {
	const eachKind = 30
	const slowEnough = 3 * time.Second

	stages := stagesFor(t, 10)
	stored := 0
	index := 0
	absorb := func(errored bool, duration time.Duration) {
		t.Helper()
		result, err := stages.absorbMessage(
			invariantStart, traceMessageLasting(t, index, errored, duration), true)
		if err != nil {
			t.Fatalf("absorbMessage() = %v, want no error", err)
		}
		stored += len(result.rows)
		index++
	}
	for range eachKind {
		absorb(false, spanDuration) // healthy: the sampler keeps one in ten
		absorb(false, slowEnough)   // slow: kept whole
		absorb(true, spanDuration)  // failed: kept whole
	}
	stored += len(stages.buffer.Expire(invariantStart.Add(time.Minute)))

	const wantSpans = eachKind * 3 * spansPerTrace
	const wantErrors = eachKind * spansPerTrace

	requests, errors, sampledDurations := rollupTotals(stages.counters.DrainAll())
	if requests != wantSpans || errors != wantErrors {
		t.Errorf("the rollup counted %d requests and %d errors, want %d and %d",
			requests, errors, wantSpans, wantErrors)
	}
	// The error rate the screen would show. One span in three failed here, and
	// reading storage instead would put it near two in three.
	if rate := float64(errors) / float64(requests); rate < 0.33 || rate > 0.34 {
		t.Errorf("error rate = %.4f, want a third", rate)
	}
	// Latency comes from every span too, not from the survivors — which are
	// enriched with exactly the slow and failed ones.
	if sampledDurations != wantSpans {
		t.Errorf("the rollup's latency sample holds %d durations, want all %d",
			sampledDurations, wantSpans)
	}

	// And the sampler really did drop most of the healthy traces, or none of
	// the above proves anything.
	if stored >= wantSpans {
		t.Fatalf("storage kept %d of %d spans; the sampler is not sampling, so the "+
			"exactness above proves nothing", stored, wantSpans)
	}
}

// A redelivered message must not be counted twice, or a rebalance quietly
// inflates every count it touched.
func TestARedeliveredMessageIsWrittenAgainButNotCountedAgain(t *testing.T) {
	stages := stagesFor(t, 100)
	message := traceMessage(t, 1, false)

	first, err := stages.absorbMessage(invariantStart, message, true)
	if err != nil {
		t.Fatalf("absorbMessage() = %v, want no error", err)
	}
	replay, err := stages.absorbMessage(invariantStart, message, false)
	if err != nil {
		t.Fatalf("absorbMessage() on the replay = %v, want no error", err)
	}

	if got := len(first.rows) + len(replay.rows) +
		len(stages.buffer.Expire(invariantStart.Add(time.Minute))); got != spansPerTrace*2 {
		t.Errorf("storage saw %d rows, want the replay written again (%d)", got, spansPerTrace*2)
	}
	drained := stages.counters.DrainAll()
	counted := totalsByMetric(drained)
	if counted[derive.MetricSpanCount] != spansPerTrace {
		t.Errorf("%s = %v, want %d: the replay must not be counted twice",
			derive.MetricSpanCount, counted[derive.MetricSpanCount], spansPerTrace)
	}
	if requests, _, _ := rollupTotals(drained); requests != spansPerTrace {
		t.Errorf("the endpoint rollup counted %d requests, want %d: the replay must not "+
			"be counted twice there either", requests, spansPerTrace)
	}
}

// The mask stage runs before everything else so that no later stage can see an
// original value. This checks the outcome rather than the ordering: nothing
// sensitive survives into a row bound for ClickHouse.
func TestNoUnmaskedValueReachesARow(t *testing.T) {
	const (
		bearerToken = "Bearer supersecrettokenvalue"
		cardNumber  = "4111111111111111"
		password    = "hunter2"
	)
	stages := stagesFor(t, 100)

	message := traceMessage(t, 1, false,
		attribute("http.request.header.authorization", stringValue(bearerToken)),
		attribute("db.password", stringValue(password)),
		attribute("payment.reference", stringValue(cardNumber)),
		attribute("http.route", stringValue("/checkout")),
	)

	result, err := stages.absorbMessage(invariantStart, message, true)
	if err != nil {
		t.Fatalf("absorbMessage() = %v, want no error", err)
	}
	rows := append(result.rows, stages.buffer.Expire(invariantStart.Add(time.Minute))...)
	if len(rows) == 0 {
		t.Fatal("no rows reached storage, so this proves nothing")
	}

	for _, row := range rows {
		for _, document := range []string{row.SpanAttrs, row.ResourceAttrs, row.Events} {
			for _, secret := range []string{bearerToken, cardNumber, password} {
				if strings.Contains(document, secret) {
					t.Fatalf("a masked value reached storage in %q", document)
				}
			}
		}
		// The route is what someone came to read; masking is not allowed to
		// take it with the secrets.
		if !strings.Contains(row.SpanAttrs, "/checkout") {
			t.Errorf("span attributes lost the route: %q", row.SpanAttrs)
		}
	}
}

// A batch's offsets may only move once its spans have left the buffer and its
// counters have been written. Committing early is how a crash turns into a
// hole in the data.
//
// The queue is the loop's; what is asserted here is the answer trace assembly
// gives it, so the two conditions are set up through the real counters: a batch
// that fed no bucket is trivially counted, and one that fed the minute it was
// polled in is not counted until that minute has closed.
func TestOffsetsWaitForBothTheBufferAndTheCounters(t *testing.T) {
	pipeline := Pipeline{stages: stagesFor(t, 100)}
	minute := invariantStart.Truncate(time.Minute)

	cases := map[string]struct {
		now           time.Time
		latestMinute  time.Time
		wantCommitted int
	}{
		"spans still buffered": {
			now: invariantStart.Add(5 * time.Second), wantCommitted: 0,
		},
		"counters not yet written": {
			now: invariantStart.Add(30 * time.Second), latestMinute: minute, wantCommitted: 0,
		},
		"both stored": {
			now: invariantStart.Add(90 * time.Second), latestMinute: minute, wantCommitted: 2,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			var queue consume.Queue[spanStorage]
			queue.Hold(consume.Batch[spanStorage]{
				Positions: fakePositions(2),
				Produced: spanStorage{
					spansStoredAt: invariantStart.Add(10 * time.Second),
					latestMinute:  tc.latestMinute,
				},
			})

			due := queue.Ready(storedAt(&pipeline, tc.now))
			if len(due) != tc.wantCommitted {
				t.Errorf("Ready() returned %d records, want %d", len(due), tc.wantCommitted)
			}
			if queue.Records() != 2-tc.wantCommitted {
				t.Errorf("Records() = %d, want the uncommitted records still held", queue.Records())
			}
		})
	}
}

// An edge does not exist until its partner span arrives, so it settles later
// than the counters for the same minute: a whole PairWindow later. Offsets must
// wait for it, or a crash between the commit and the drain loses edges that
// Kafka will never redeliver — the pairing buffer is process memory only.
//
// This exists because the gate was written and never connected. The field, the
// predicate and the value all existed; the line folding the value into the
// batch's storage record did not, so Drained was asked about a zero minute and
// answered "already stored" every time. Nothing failed, and the build was green.
func TestOffsetsWaitForEdgesAsWellAsCounters(t *testing.T) {
	pipeline := Pipeline{stages: stagesFor(t, 100)}
	minute := invariantStart.Truncate(time.Minute)

	// Both gates are measured from the bucket, not from the batch. Counters
	// close at minute+1m; edges close a PairWindow later, which stagesFor sets
	// to the 10s sampling window. Between the two is where the difference lives.
	countersClosed := minute.Add(time.Minute + time.Second)
	edgesClosed := minute.Add(2 * time.Minute)

	var queue consume.Queue[spanStorage]
	queue.Hold(consume.Batch[spanStorage]{
		Positions: fakePositions(2),
		Produced: spanStorage{
			spansStoredAt:    minute,
			latestMinute:     minute,
			latestEdgeMinute: minute,
		},
	})

	if due := queue.Ready(storedAt(&pipeline, countersClosed)); len(due) != 0 {
		t.Errorf("released %d records while the edge minute was still open — the edge gate is "+
			"not being consulted, and a crash here would lose edges Kafka cannot redeliver", len(due))
	}

	if due := queue.Ready(storedAt(&pipeline, edgesClosed)); len(due) != 2 {
		t.Errorf("released %d records once everything was stored, want 2", len(due))
	}
}

// Kafka commits move a partition's offset to a point, so releasing a later
// batch before an earlier one would declare the earlier one done.
func TestCommitsAreReleasedInOrder(t *testing.T) {
	pipeline := Pipeline{stages: stagesFor(t, 100)}

	var queue consume.Queue[spanStorage]
	queue.Hold(consume.Batch[spanStorage]{
		Positions: fakePositions(1),
		Produced:  spanStorage{spansStoredAt: invariantStart.Add(time.Minute)},
	})
	queue.Hold(consume.Batch[spanStorage]{
		Positions: fakePositions(1),
		Produced:  spanStorage{spansStoredAt: invariantStart},
	})

	if due := queue.Ready(storedAt(&pipeline, invariantStart.Add(time.Second))); len(due) != 0 {
		t.Errorf("Ready() released %d records past a batch that is not stored yet", len(due))
	}
	if due := queue.Ready(storedAt(&pipeline, invariantStart.Add(2*time.Minute))); len(due) != 2 {
		t.Errorf("Ready() released %d records once both batches were stored, want 2", len(due))
	}
}

// storedAt asks the pipeline, at one instant, whether a batch's output has
// reached storage — which is what the loop hands the commit queue.
func storedAt(pipeline *Pipeline, now time.Time) func(spanStorage) bool {
	return func(produced spanStorage) bool { return pipeline.Stored(now, produced) }
}

func fakePositions(count int) []consume.Position {
	positions := make([]consume.Position, 0, count)
	for index := range count {
		positions = append(positions, consume.PositionOf(&kgo.Record{
			Topic: "spans-raw", Partition: 0, Offset: int64(index),
		}))
	}
	return positions
}
