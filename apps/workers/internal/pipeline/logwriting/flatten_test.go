package logwriting

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"

	logsvc "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

var (
	testTraceID = []byte{
		0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
		0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10,
	}
	testSpanID = []byte{0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18}
)

const (
	eventNanos    = int64(1_700_000_000_000_000_000)
	observedNanos = int64(1_700_000_111_000_000_000)
)

var receivedAt = time.Unix(0, 1_700_000_222_000_000_000).UTC()

func testStamps() stamps {
	return stamps{receivedAt: receivedAt, retentionDays: 30}
}

// A zero timestamp partitions to 1970 and the table's TTL deletes the row on
// arrival, so the fallback chain is what keeps a clockless record.
func TestTimestampFallsBackThroughTheClocks(t *testing.T) {
	cases := map[string]struct {
		record     *logspb.LogRecord
		wantTime   time.Time
		wantSource string
	}{
		"event time when it has one": {
			record: &logspb.LogRecord{
				TimeUnixNano:         uint64(eventNanos),
				ObservedTimeUnixNano: uint64(observedNanos),
			},
			wantTime:   time.Unix(0, eventNanos).UTC(),
			wantSource: timestampSourceEvent,
		},
		"observed time when the event time is absent": {
			record:     &logspb.LogRecord{ObservedTimeUnixNano: uint64(observedNanos)},
			wantTime:   time.Unix(0, observedNanos).UTC(),
			wantSource: timestampSourceObserved,
		},
		"our own clock when it carries neither": {
			record:     &logspb.LogRecord{},
			wantTime:   receivedAt,
			wantSource: timestampSourceReceived,
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got, source := recordTime(tc.record, receivedAt)
			if !got.Equal(tc.wantTime) {
				t.Errorf("timestamp = %s, want %s", got, tc.wantTime)
			}
			if source != tc.wantSource {
				t.Errorf("timestamp_source = %q, want %q", source, tc.wantSource)
			}
			if got.IsZero() {
				t.Error("timestamp is zero, which the TTL deletes on arrival")
			}
		})
	}
}

func TestBodyIsRenderedTextBesideTheTypedMap(t *testing.T) {
	cases := map[string]struct {
		body           *commonpb.AnyValue
		wantText       string
		wantStructured string
	}{
		"a string body is the text itself": {
			body:           stringValue("payment declined"),
			wantText:       "payment declined",
			wantStructured: otlpvalue.EmptyObject,
		},
		"a map body is stored both ways": {
			body: &commonpb.AnyValue{Value: &commonpb.AnyValue_KvlistValue{
				KvlistValue: &commonpb.KeyValueList{Values: []*commonpb.KeyValue{
					{Key: "event", Value: stringValue("checkout")},
				}},
			}},
			wantText:       `{"event":"checkout"}`,
			wantStructured: `{"event":"checkout"}`,
		},
		"a number body keeps its JSON text": {
			body:           &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: 42}},
			wantText:       "42",
			wantStructured: otlpvalue.EmptyObject,
		},
		"an array body keeps its JSON text": {
			body: &commonpb.AnyValue{Value: &commonpb.AnyValue_ArrayValue{
				ArrayValue: &commonpb.ArrayValue{Values: []*commonpb.AnyValue{
					stringValue("a"), stringValue("b"),
				}},
			}},
			wantText:       `["a","b"]`,
			wantStructured: otlpvalue.EmptyObject,
		},
		"an absent body is empty rather than null": {
			body:           nil,
			wantText:       "",
			wantStructured: otlpvalue.EmptyObject,
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			text, structured := renderBody(tc.body)
			if text != tc.wantText {
				t.Errorf("body = %q, want %q", text, tc.wantText)
			}
			if structured != tc.wantStructured {
				t.Errorf("body_structured = %q, want %q", structured, tc.wantStructured)
			}
		})
	}
}

func TestFlattenSplitsAttributesAndPromotesTheException(t *testing.T) {
	payload := decoded{
		envelope: &nexovuev1.Envelope{TenantId: "tenant-a", ProjectId: "project-a"},
		request:  requestWithOneRecord(errorRecord()),
	}

	rows := payload.rows(testStamps())
	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(rows))
	}
	row := rows[0]

	// Attributes are stored in the nested shape ClickHouse gives them, so the
	// assertion is on the path rather than on the text.
	var resource map[string]any
	if err := json.Unmarshal([]byte(row.ResourceAttrs), &resource); err != nil {
		t.Fatalf("resource_attrs is not JSON: %v (%q)", err, row.ResourceAttrs)
	}
	if otlpvalue.AttributeAt(resource, "service.name") != "checkout" {
		t.Errorf("resource_attrs = %q, want service.name checkout", row.ResourceAttrs)
	}
	if row.LogAttrs == otlpvalue.EmptyObject || row.ResourceAttrs == row.LogAttrs {
		t.Errorf("log_attrs = %q, want the record's own attributes", row.LogAttrs)
	}
	if row.ExceptionType != "ValueError" || row.ExceptionMessage != "bad card" {
		t.Errorf("exception = (%q, %q), want (ValueError, bad card)",
			row.ExceptionType, row.ExceptionMessage)
	}
	if row.ServiceName != "checkout" {
		t.Errorf("service_name = %q, want checkout", row.ServiceName)
	}
	if row.TenantID != "tenant-a" || row.ProjectID != "project-a" {
		t.Errorf("tenancy = (%q, %q), want it from the envelope", row.TenantID, row.ProjectID)
	}
	if row.RetentionDays != 30 {
		t.Errorf("retention_days = %d, want 30; a zero expires the row instantly", row.RetentionDays)
	}
	if !row.ReceivedAt.Equal(receivedAt) {
		t.Errorf("received_at = %s, want our own clock %s", row.ReceivedAt, receivedAt)
	}
}

// The id columns are FixedString, so they must be written at exactly their
// declared width whether the record carried a trace context or not.
func TestIDsAreHexAtTheDeclaredWidth(t *testing.T) {
	payload := decoded{
		envelope: &nexovuev1.Envelope{},
		request:  requestWithOneRecord(errorRecord()),
	}
	row := payload.rows(testStamps())[0]

	if len(row.TraceID) != otlpvalue.TraceIDHexLen || len(row.SpanID) != otlpvalue.SpanIDHexLen {
		t.Fatalf("id widths = (%d, %d), want (%d, %d)",
			len(row.TraceID), len(row.SpanID), otlpvalue.TraceIDHexLen, otlpvalue.SpanIDHexLen)
	}
	if string(row.TraceID) != hex.EncodeToString(testTraceID) {
		t.Errorf("trace_id = %q, want the hex of the record's id", row.TraceID)
	}

	untraced := decoded{
		envelope: &nexovuev1.Envelope{},
		request:  requestWithOneRecord(&logspb.LogRecord{}),
	}
	bare := untraced.rows(testStamps())[0]
	if len(bare.TraceID) != otlpvalue.TraceIDHexLen || len(bare.SpanID) != otlpvalue.SpanIDHexLen {
		t.Errorf("a record with no trace context wrote ids of width (%d, %d)",
			len(bare.TraceID), len(bare.SpanID))
	}
}

func TestDecodeRefusesAnotherSignal(t *testing.T) {
	message, err := proto.Marshal(&nexovuev1.Envelope{
		Signal:    nexovuev1.Signal_SIGNAL_SPANS,
		TenantId:  "tenant-a",
		ProjectId: "project-a",
	})
	if err != nil {
		t.Fatalf("marshal envelope: %v", err)
	}
	if _, err := decodeEnvelope(message); err == nil {
		t.Fatal("decodeEnvelope accepted a span envelope off the logs topic")
	}
}

func TestDecodeRejectsAMalformedPayloadWithItsTenancy(t *testing.T) {
	message, err := proto.Marshal(&nexovuev1.Envelope{
		Signal:    nexovuev1.Signal_SIGNAL_LOGS,
		TenantId:  "tenant-a",
		ProjectId: "project-a",
		Payload:   []byte{0xff, 0xff, 0xff},
	})
	if err != nil {
		t.Fatalf("marshal envelope: %v", err)
	}

	// A parked message bypassed masking, so it must carry the tenant it
	// belonged to: unattributed customer data is data nobody can delete.
	_, err = decodeEnvelope(message)
	var rejection *consume.Rejection
	if !errors.As(err, &rejection) {
		t.Fatalf("error %v is not a rejection carrying its tenancy", err)
	}
	if rejection.TenantID != "tenant-a" || rejection.ProjectID != "project-a" {
		t.Errorf("rejection tenancy = (%q, %q), want it from the envelope",
			rejection.TenantID, rejection.ProjectID)
	}
}

func requestWithOneRecord(record *logspb.LogRecord) *logsvc.ExportLogsServiceRequest {
	return &logsvc.ExportLogsServiceRequest{
		ResourceLogs: []*logspb.ResourceLogs{{
			Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
				{Key: attrServiceName, Value: stringValue("checkout")},
			}},
			ScopeLogs: []*logspb.ScopeLogs{{LogRecords: []*logspb.LogRecord{record}}},
		}},
	}
}

func errorRecord() *logspb.LogRecord {
	return &logspb.LogRecord{
		TimeUnixNano: uint64(eventNanos),
		SeverityText: "ERROR",
		TraceId:      testTraceID,
		SpanId:       testSpanID,
		Body:         stringValue("payment declined"),
		Attributes: []*commonpb.KeyValue{
			{Key: otlpvalue.AttrExceptionType, Value: stringValue("ValueError")},
			{Key: otlpvalue.AttrExceptionMessage, Value: stringValue("bad card")},
		},
	}
}

func stringValue(text string) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: text}}
}
