package main

import (
	"encoding/hex"
	"fmt"
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

// spanDuration is what every probe span reports. Well under
// WORKERS_SAMPLING_SLOW_THRESHOLD, so "slow" is never the reason a trace is
// kept — the error status is, and that is deterministic.
const spanDuration = 5 * time.Millisecond

// severityInfo is OTLP's INFO number, which the store's sort key orders by.
const severityInfo = 9

// traceRequest is one trace's spans, shaped exactly as the receiver shapes
// them: one envelope per trace, because that is what the assembler expects to
// find on a partition.
//
// Every span is given ERROR status. The tail sampler keeps error traces whole
// under the shipped default policy, so every probe span reaches storage without
// changing any sampling setting — which is what makes loss countable at all.
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
		records = append(records, &tracepb.Span{
			TraceId:           trace,
			SpanId:            id,
			Name:              "nvprobe",
			Kind:              tracepb.Span_SPAN_KIND_SERVER,
			StartTimeUnixNano: uint64(now.UnixNano()),                   //nolint:gosec // a wall clock is positive
			EndTimeUnixNano:   uint64(now.Add(spanDuration).UnixNano()), //nolint:gosec // a wall clock is positive
			Status:            &tracepb.Status{Code: tracepb.Status_STATUS_CODE_ERROR},
		})
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
