package main

import (
	"testing"
	"time"

	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
)

// The rig's shape, as arm94 runs it: a twelve-partition topic whose partitions
// split evenly between the resident worker and the peer that joins, and one
// minute of the probe's shipped rate.
const (
	rigPartitions      = 12
	rigTracesPerSecond = 20
	rigSpansPerTrace   = 5
	tracesPerMinute    = rigTracesPerSecond * 60
)

// nanosPerMillisecond is how the worker turns a span's two timestamps into the
// duration its counters sum — `durationMS` in
// apps/workers/internal/pipeline/traceassembly/flatten.go. The test computes
// the same number, so what it compares is what reaches the store.
const nanosPerMillisecond = 1e6

// partialBlock is one worker's share of a series-minute, reduced to the fields
// two workers' shares can differ in. Everything else in the insert — the
// minute, the tenancy, the service, the endpoint, the attributes — is the
// series itself and is equal between them by construction.
//
// `latencyMS` is the endpoint rollup's and the edge accumulator's own field:
// they carry the durations behind the row, not just their total.
type partialBlock struct {
	spans      int
	errors     int
	durationMS float64
	latencyMS  []float64
}

func (b *partialBlock) observe(span *tracepb.Span) {
	duration := float64(span.GetEndTimeUnixNano()-span.GetStartTimeUnixNano()) / nanosPerMillisecond
	b.spans++
	b.durationMS += duration
	b.latencyMS = append(b.latencyMS, duration)
	if span.GetStatus().GetCode() == tracepb.Status_STATUS_CODE_ERROR {
		b.errors++
	}
}

func isServer(span *tracepb.Span) bool { return span.GetKind() == tracepb.Span_SPAN_KIND_SERVER }

func isClient(span *tracepb.Span) bool { return span.GetKind() == tracepb.Span_SPAN_KIND_CLIENT }

// blockFrom folds one minute of the probe's traffic on the given partitions
// into the block a worker holding them would derive. The wall clock is fixed:
// a span's duration is its end minus its start, so the clock cannot be what
// makes two blocks differ.
func blockFrom(t *testing.T, holds func(int32) bool, reaches func(*tracepb.Span) bool) partialBlock {
	t.Helper()
	started := time.Date(2026, 9, 4, 11, 8, 0, 0, time.UTC)

	var block partialBlock
	for sequence := range uint32(tracesPerMinute) {
		partition := int32(sequence % rigPartitions) //nolint:gosec // partition counts are small
		if !holds(partition) {
			continue
		}
		request, err := traceRequest("ed3ebae1", partition, sequence, rigSpansPerTrace, started)
		if err != nil {
			t.Fatalf("traceRequest(sequence %d): %v", sequence, err)
		}
		for _, span := range request.GetResourceSpans()[0].GetScopeSpans()[0].GetSpans() {
			if reaches(span) {
				block.observe(span)
			}
		}
	}
	return block
}

// TestTwoWorkersSharesOfAMinuteDeriveDifferentBlocks is the probe's guard
// against being the thing it measures.
//
// ClickHouse deduplicates an insert by the block's content (ADRs 0060 and
// 0075), and `metrics_sum`, `endpoint_stats_1m` and `service_edges_1m` all have
// that window on. Two workers holding an even split of one series-minute each
// insert a partial block for it. If those two blocks are identical the store
// keeps one and silently drops the other — and the run reads as loss that the
// handover fix did not cause. Counts cannot tell them apart, because an even
// split is the case being measured; the durations behind them have to.
func TestTwoWorkersSharesOfAMinuteDeriveDifferentBlocks(t *testing.T) {
	resident := func(partition int32) bool { return partition < rigPartitions/2 }
	peer := func(partition int32) bool { return !resident(partition) }

	for _, stage := range []struct {
		table   string
		reaches func(*tracepb.Span) bool
	}{
		{"metrics_sum", func(*tracepb.Span) bool { return true }},
		{"endpoint_stats_1m", isServer},
		{"service_edges_1m", isClient},
	} {
		t.Run(stage.table, func(t *testing.T) {
			held := blockFrom(t, resident, stage.reaches)
			taken := blockFrom(t, peer, stage.reaches)

			// The split has to be even, or the counts alone would separate the
			// two blocks and the test would prove nothing.
			if held.spans != taken.spans || held.errors != taken.errors {
				t.Fatalf("shares are not an even split: resident %d spans/%d errors, peer %d spans/%d errors",
					held.spans, held.errors, taken.spans, taken.errors)
			}
			if held.spans == 0 {
				t.Fatal("no spans reached this stage; the split or the filter is wrong")
			}

			if held.durationMS == taken.durationMS {
				t.Errorf("both shares sum to %.6f ms over %d spans, so their %s blocks are identical "+
					"and the store drops one", held.durationMS, held.spans, stage.table)
			}
			if sameLatencies(held.latencyMS, taken.latencyMS) {
				t.Errorf("both shares carry the same %d durations, so their %s rows are identical "+
					"and the store drops one", len(held.latencyMS), stage.table)
			}
		})
	}
}

// sameLatencies reports whether two shares carry indistinguishable durations.
// The rollup sorts them before they reach the row, so order is not a difference
// the store would see.
func sameLatencies(held, taken []float64) bool {
	if len(held) != len(taken) {
		return false
	}
	counts := make(map[float64]int, len(held))
	for _, duration := range held {
		counts[duration]++
	}
	for _, duration := range taken {
		counts[duration]--
		if counts[duration] < 0 {
			return false
		}
	}
	return true
}

// TestASpansDurationIsDerivedFromItsIdentity holds the other half of the same
// contract: a record the new owner re-reads must derive the same duration, or
// the store could not recognise a genuine replay of a block either. This one
// passes before the durations were varied as well as after — it is the guard on
// the fix, not the control that proves it was needed.
func TestASpansDurationIsDerivedFromItsIdentity(t *testing.T) {
	first := blockFrom(t, func(partition int32) bool { return partition == 3 }, isServer)
	replayed := blockFrom(t, func(partition int32) bool { return partition == 3 }, isServer)

	if !sameLatencies(first.latencyMS, replayed.latencyMS) {
		t.Error("the same records derived different durations, so a replay would not be recognised")
	}
	if first.durationMS != replayed.durationMS {
		t.Errorf("duration sum %.6f then %.6f ms for the same records", first.durationMS, replayed.durationMS)
	}
}

// TestEveryProbeSpanStaysWellUnderTheSlowThreshold keeps the varied durations
// inside the bound the constant used to hold on its own:
// WORKERS_SAMPLING_SLOW_THRESHOLD is 2s, and a probe span that reached it would
// be kept for being slow rather than for being an error, which is the property
// that makes every span's arrival countable.
func TestEveryProbeSpanStaysWellUnderTheSlowThreshold(t *testing.T) {
	const slowThreshold = 2 * time.Second

	longest := time.Duration(0)
	for sequence := range uint32(tracesPerMinute) {
		request, err := traceRequest("ed3ebae1", 0, sequence, rigSpansPerTrace, time.Unix(0, 0).UTC())
		if err != nil {
			t.Fatalf("traceRequest(sequence %d): %v", sequence, err)
		}
		for _, span := range request.GetResourceSpans()[0].GetScopeSpans()[0].GetSpans() {
			//nolint:gosec // both are wall-clock nanoseconds this test just built
			if held := time.Duration(span.GetEndTimeUnixNano() - span.GetStartTimeUnixNano()); held > longest {
				longest = held
			}
		}
	}
	if longest*10 > slowThreshold {
		t.Errorf("longest probe span is %s, want well under the %s slow threshold", longest, slowThreshold)
	}
	if longest == 0 {
		t.Fatal("no span had a duration at all")
	}
}

// TestOnlyTheFirstSpanOfATraceOpensAnEdge pins the payload shape each stage's
// denominator is read from: one CLIENT naming a peer nothing reports for, and
// the rest SERVERs, all of them parentless so no SERVER can ever pair with the
// CLIENT and turn one trace into two edges.
func TestOnlyTheFirstSpanOfATraceOpensAnEdge(t *testing.T) {
	request, err := traceRequest("ed3ebae1", 0, 0, rigSpansPerTrace, time.Unix(0, 0).UTC())
	if err != nil {
		t.Fatalf("traceRequest: %v", err)
	}
	spans := request.GetResourceSpans()[0].GetScopeSpans()[0].GetSpans()
	if len(spans) != rigSpansPerTrace {
		t.Fatalf("trace holds %d spans, want %d", len(spans), rigSpansPerTrace)
	}

	for index, span := range spans {
		if len(span.GetParentSpanId()) != 0 {
			t.Errorf("span %d has a parent, so it could pair with the caller", index)
		}
		if index == callerSpanIndex {
			if !isClient(span) {
				t.Errorf("span %d is %s, want CLIENT", index, span.GetKind())
			}
			if peer := attributeValue(span, attrPeerService); peer != peerService("ed3ebae1") {
				t.Errorf("caller names peer %q, want %q", peer, peerService("ed3ebae1"))
			}
			continue
		}
		if !isServer(span) {
			t.Errorf("span %d is %s, want SERVER", index, span.GetKind())
		}
		if peer := attributeValue(span, attrPeerService); peer != "" {
			t.Errorf("span %d names peer %q; only the caller may", index, peer)
		}
	}
}

func attributeValue(span *tracepb.Span, key string) string {
	for _, attribute := range span.GetAttributes() {
		if attribute.GetKey() == key {
			return attribute.GetValue().GetStringValue()
		}
	}
	return ""
}

// TestTheReportNamesEachStagesDenominator checks the arithmetic the counting
// queries are compared against: every accepted span is a `metrics_sum`
// denominator, one span per message is the edge denominator, and the rest are
// the endpoint rollup's.
func TestTheReportNamesEachStagesDenominator(t *testing.T) {
	const messages = 7199
	spans := &tally{rowsPerMessage: rigSpansPerTrace}
	for range messages {
		spans.accepted(411)
	}

	body := reportFor("ed3ebae1", settings{spanPartitions: rigPartitions, logPartitions: 4}, spans, &tally{})

	if want := int64(messages * rigSpansPerTrace); body.SpansAccepted != want {
		t.Errorf("spans_accepted = %d, want %d", body.SpansAccepted, want)
	}
	if body.EdgeCallsAccepted != messages {
		t.Errorf("edge_calls_accepted = %d, want one per message (%d)", body.EdgeCallsAccepted, messages)
	}
	if want := int64(messages * (rigSpansPerTrace - 1)); body.ServerSpansAccepted != want {
		t.Errorf("server_spans_accepted = %d, want %d", body.ServerSpansAccepted, want)
	}
	if got := body.EdgeCallsAccepted + body.ServerSpansAccepted; got != body.SpansAccepted {
		t.Errorf("the two denominators total %d, want every accepted span (%d)", got, body.SpansAccepted)
	}
	if body.SpanPayloadBytes != int64(messages*411) {
		t.Errorf("span_payload_bytes_accepted = %d, want %d", body.SpanPayloadBytes, messages*411)
	}
}
