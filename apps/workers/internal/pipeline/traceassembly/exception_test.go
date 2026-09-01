package traceassembly

import (
	"encoding/json"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// exceptionEvent is one OTLP exception event, built from the attribute names
// the conventions define.
func exceptionEvent(attributes ...*commonpb.KeyValue) *tracepb.Span_Event {
	return &tracepb.Span_Event{
		TimeUnixNano: 1_700_000_000_000_000_000,
		Name:         otlpvalue.ExceptionEventName,
		Attributes:   attributes,
	}
}

// The defect this file exists for: the columns error grouping reads were never
// written, so a service whose exceptions arrive the standard OpenTelemetry way
// — as a span event — reported errors and no exceptions at the same time.
func TestAnExceptionEventFillsThePromotedColumns(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		events: []*tracepb.Span_Event{exceptionEvent(
			attribute(otlpvalue.AttrExceptionType, stringValue("grpc._channel._InactiveRpcError")),
			attribute(otlpvalue.AttrExceptionMessage, stringValue("failed to connect to all addresses")),
			attribute("exception.stacktrace", stringValue("Traceback (most recent call last):\n  ...")),
			attribute("exception.escaped", stringValue("False")),
		)},
	}))

	if row.ExceptionType != "grpc._channel._InactiveRpcError" {
		t.Errorf("ExceptionType = %q, want the event's exception.type", row.ExceptionType)
	}
	if row.ExceptionMessage != "failed to connect to all addresses" {
		t.Errorf("ExceptionMessage = %q, want the event's exception.message", row.ExceptionMessage)
	}
}

// The stacktrace is not a column. It is large and never filtered on, so it stays
// in `events` — which is where the API reads it back from.
func TestTheStacktraceStaysInEventsRatherThanBecomingAColumn(t *testing.T) {
	const stacktrace = "Traceback (most recent call last):\n  File \"main.py\", line 1"

	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		events: []*tracepb.Span_Event{exceptionEvent(
			attribute(otlpvalue.AttrExceptionType, stringValue("TimeoutError")),
			attribute(otlpvalue.AttrExceptionMessage, stringValue("deadline exceeded")),
			attribute("exception.stacktrace", stringValue(stacktrace)),
		)},
	}))

	if row.ExceptionMessage == stacktrace {
		t.Fatal("the stacktrace was promoted into exception_message")
	}
	if got := storedEventAttribute(t, row, "exception.stacktrace"); got != stacktrace {
		t.Errorf("events[0] stacktrace = %q, want it stored whole", got)
	}
}

// A span records several exceptions when a client retries inside it. The columns
// are singular, so the first recorded throw wins — and it must be one the API can
// find again in `events` when it goes looking for the matching stacktrace.
func TestThePromotedExceptionIsTheFirstOneRecorded(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		events: []*tracepb.Span_Event{
			{TimeUnixNano: 1, Name: "retrying"},
			exceptionEvent(
				attribute(otlpvalue.AttrExceptionType, stringValue("ConnectionError")),
				attribute(otlpvalue.AttrExceptionMessage, stringValue("connection reset by peer")),
			),
			exceptionEvent(
				attribute(otlpvalue.AttrExceptionType, stringValue("RuntimeError")),
				attribute(otlpvalue.AttrExceptionMessage, stringValue("giving up after 3 attempts")),
			),
		},
	}))

	if row.ExceptionType != "ConnectionError" || row.ExceptionMessage != "connection reset by peer" {
		t.Errorf("promoted %q/%q, want the first exception event", row.ExceptionType, row.ExceptionMessage)
	}

	// Nothing is lost: the exception that was not promoted is still stored.
	var events []spanEvent
	if err := json.Unmarshal([]byte(row.Events), &events); err != nil {
		t.Fatalf("events is not JSON: %v (%q)", err, row.Events)
	}
	if len(events) != 3 {
		t.Fatalf("events holds %d entries, want all three", len(events))
	}
	if got := otlpvalue.AttributeAt(events[2].Attributes, otlpvalue.AttrExceptionType); got != "RuntimeError" {
		t.Errorf("the second exception was not stored: %v", got)
	}
}

// The conventions make type and message *conditionally* required, so an event
// carrying neither says nothing and must not consume the columns a later one
// would have filled.
func TestAnEmptyExceptionEventDoesNotConsumeTheColumns(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		events: []*tracepb.Span_Event{
			exceptionEvent(attribute("exception.escaped", stringValue("False"))),
			exceptionEvent(attribute(otlpvalue.AttrExceptionMessage, stringValue("disk full"))),
		},
	}))

	if row.ExceptionType != "" {
		t.Errorf("ExceptionType = %q, want empty — no event named a type", row.ExceptionType)
	}
	if row.ExceptionMessage != "disk full" {
		t.Errorf("ExceptionMessage = %q, want the first event that recorded something", row.ExceptionMessage)
	}
}

// The grouping query keeps only rows whose exception_type is non-empty, so
// anything that is not an exception must leave the columns empty rather than
// inventing an error group.
func TestNonExceptionEventsLeaveTheColumnsEmpty(t *testing.T) {
	notAnException := []*commonpb.KeyValue{
		attribute(otlpvalue.AttrExceptionType, stringValue("NotAnException")),
	}
	cases := map[string]spanOptions{
		"no events at all": {},
		"an event of another name carrying exception attributes": {
			events: []*tracepb.Span_Event{{TimeUnixNano: 1, Name: "message", Attributes: notAnException}},
		},
		"exception attributes on the span rather than an event": {attributes: notAnException},
	}

	for name, options := range cases {
		t.Run(name, func(t *testing.T) {
			row := flattenOne(t, marshalEnvelope(t, "1.27.0", options))

			if row.ExceptionType != "" || row.ExceptionMessage != "" {
				t.Errorf("promoted %q/%q, want both empty", row.ExceptionType, row.ExceptionMessage)
			}
		})
	}
}

// storedEventAttribute reads one attribute back out of the first stored event.
func storedEventAttribute(t *testing.T, row spanRow, key string) string {
	t.Helper()

	var events []spanEvent
	if err := json.Unmarshal([]byte(row.Events), &events); err != nil {
		t.Fatalf("events is not JSON: %v (%q)", err, row.Events)
	}
	if len(events) == 0 {
		t.Fatal("events is empty")
	}
	value, _ := otlpvalue.AttributeAt(events[0].Attributes, key).(string)
	return value
}
