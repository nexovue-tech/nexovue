package main

import (
	"encoding/hex"
	"fmt"
	"hash/fnv"
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"

	logsvc "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
)

// attrServiceName is the resource attribute both stores promote to a column.
const attrServiceName = "service.name"

// attrPeerService is what a caller says it called. It is what turns the probe's
// first span into a row of `service_edges_1m`: nothing answers it, so the edge
// accumulator names the callee from this after the pairing window expires —
// exactly one edge per trace, with no second service to instrument.
const attrPeerService = "service.peer.name"

// callerSpanIndex is the one span of a trace that opens an edge.
const callerSpanIndex = 0

// minimumSpanDuration is the shortest a probe span reports and
// spanDurationSpread how far above it they reach. Both stay far under
// WORKERS_SAMPLING_SLOW_THRESHOLD (2s), so "slow" is never the reason a trace
// is kept — the error status is, and that is deterministic.
const (
	minimumSpanDuration = 5 * time.Millisecond
	spanDurationSpread  = 4 * time.Millisecond
)

// durationOf gives every span a duration of its own, derived from that span's
// identity so re-reading the same record derives the same value.
//
// One duration for all of them would make the probe the thing it measures: two
// workers holding an even split of a series-minute derive counter blocks whose
// counts are equal by definition, so only the durations can separate them — and
// identical blocks are what ClickHouse silently drops as a retry. Hashed rather
// than linear in the sequence, because the rig's partition
// is that sequence modulo the partition count, and any linear function of it
// totals the same over both halves.
func durationOf(sequence uint32, index int) time.Duration {
	identity := fnv.New64a()
	_, _ = identity.Write([]byte(spanID(sequence, index)))
	return minimumSpanDuration + time.Duration(identity.Sum64()%uint64(spanDurationSpread))
}

// severityInfo is OTLP's INFO number, which the store's sort key orders by.
const severityInfo = 9

// traceRequest is one trace's spans, shaped exactly as the receiver shapes
// them: one envelope per trace, because that is what the assembler expects to
// find on a partition.
//
// Every span is given ERROR status. The tail sampler keeps error traces whole
// under the shipped default policy, so every probe span reaches storage without
// changing any sampling setting — which is what makes loss countable at all.
//
// The first span of every trace is a CLIENT naming a peer nothing reports for,
// and the rest are SERVERs. That gives each of the three counter stages a
// denominator of its own from one run: every span counts in `metrics_sum`, only
// the SERVERs reach `endpoint_stats_1m`, and only the CLIENT makes an edge. The
// SERVERs are deliberately left parentless so they never pair with the CLIENT —
// one trace is one edge, whoever consumes it.
func traceRequest(run string, partition int32, sequence uint32, spans int, now time.Time) (
	*tracesvc.ExportTraceServiceRequest, error,
) {
	trace, err := hex.DecodeString(traceID(run, partition, sequence))
	if err != nil {
		return nil, fmt.Errorf("trace id: %w", err)
	}

	records := make([]*tracepb.Span, 0, spans)
	for index := range spans {
		id, err := hex.DecodeString(spanID(sequence, index))
		if err != nil {
			return nil, fmt.Errorf("span id: %w", err)
		}
		record := &tracepb.Span{
			TraceId:           trace,
			SpanId:            id,
			Name:              "nvprobe",
			Kind:              tracepb.Span_SPAN_KIND_SERVER,
			StartTimeUnixNano: uint64(now.UnixNano()),                                  //nolint:gosec // a wall clock is positive
			EndTimeUnixNano:   uint64(now.Add(durationOf(sequence, index)).UnixNano()), //nolint:gosec // a wall clock is positive
			Status:            &tracepb.Status{Code: tracepb.Status_STATUS_CODE_ERROR},
		}
		if index == callerSpanIndex {
			record.Kind = tracepb.Span_SPAN_KIND_CLIENT
			record.Attributes = []*commonpb.KeyValue{{
				Key: attrPeerService,
				Value: &commonpb.AnyValue{
					Value: &commonpb.AnyValue_StringValue{StringValue: peerService(run)},
				},
			}}
		}
		records = append(records, record)
	}

	return &tracesvc.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{{
			Resource:   resourceWith(spanService(run)),
			ScopeSpans: []*tracepb.ScopeSpans{{Spans: records}},
		}},
	}, nil
}

// logRequest is one message's log records, each carrying its own identity in
// its body.
func logRequest(run string, partition int32, first uint32, records int, now time.Time) (
	*logsvc.ExportLogsServiceRequest, error,
) {
	entries := make([]*logspb.LogRecord, 0, records)
	for index := range records {
		sequence := first + uint32(index) //nolint:gosec // index is a small loop counter
		entries = append(entries, &logspb.LogRecord{
			TimeUnixNano:   uint64(now.UnixNano()), //nolint:gosec // a wall clock is positive
			SeverityNumber: severityInfo,
			SeverityText:   "INFO",
			Body: &commonpb.AnyValue{
				Value: &commonpb.AnyValue_StringValue{StringValue: logBody(run, partition, sequence)},
			},
		})
	}

	return &logsvc.ExportLogsServiceRequest{
		ResourceLogs: []*logspb.ResourceLogs{{
			Resource:  resourceWith(logService(run)),
			ScopeLogs: []*logspb.ScopeLogs{{LogRecords: entries}},
		}},
	}, nil
}

func resourceWith(service string) *resourcepb.Resource {
	return &resourcepb.Resource{Attributes: []*commonpb.KeyValue{{
		Key:   attrServiceName,
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: service}},
	}}}
}
