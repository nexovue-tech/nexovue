package logwriting

import (
	"encoding/hex"
	"fmt"
	"math"
	"time"

	logsvc "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// Widths of the hex-encoded id columns, which are FixedString in ClickHouse and
// so must be written at exactly their declared size.
const (
	traceIDHexLen = 32
	spanIDHexLen  = 16
)

// attrServiceName is the resource attribute behind the promoted column the log
// sort key groups by.
const attrServiceName = "service.name"

// The record attributes an exception arrives in when it comes as a log rather
// than as a span event. OpenTelemetry is deprecating the Span Events API, so
// both shapes arrive — from different customers — for years, and promoting
// these two lets one grouping query read both eras.
const (
	attrExceptionType    = "exception.type"
	attrExceptionMessage = "exception.message"
)

// Which clock `timestamp` was taken from. OTLP makes a record's own Timestamp
// optional and defines ObservedTimestamp beside it, so a stored timestamp can
// mean three different things; recording which keeps a query from reading our
// receive time as an event time.
const (
	timestampSourceEvent    = "event"
	timestampSourceObserved = "observed"
	timestampSourceReceived = "received"
)

// logRow is one row of the `logs` table, in the order the columns are declared.
// Ids arrive hex-encoded and padded, ready for FixedString.
type logRow struct {
	Timestamp time.Time
	// ReceivedAt is our own clock. The stored row partitions and expires on it,
	// so a client's wrong clock cannot pin a row in storage or delete it early.
	ReceivedAt    time.Time
	RetentionDays uint16
	TenantID      string
	ProjectID     string
	TraceID       []byte
	SpanID        []byte
	ServiceName   string
	// Severity is the record's original text; SeverityNumber is OTLP's
	// normalised scale, which is what the sort key orders by.
	Severity       string
	SeverityNumber int32
	// Body is the rendered text the index searches; BodyStructured is the map
	// form, or `{}` for every other shape (ADR 0062).
	Body             string
	BodyStructured   string
	ResourceAttrs    string
	LogAttrs         string
	ExceptionType    string
	ExceptionMessage string
	TimestampSource  string
}

// decoded is one Kafka message unpacked: our envelope and the OTLP request it
// carries, still in protobuf form.
//
// Decoding stops here rather than continuing into rows so the mask stage can
// run on the decoded payload. Masking the payload rather than the rows is what
// guarantees an unmasked value cannot reach the store — the stages after it
// never see the original at all.
type decoded struct {
	envelope *nexovuev1.Envelope
	request  *logsvc.ExportLogsServiceRequest
}

// decodeEnvelope unpacks one Kafka message. Every failure is a
// *consume.Rejection, so the caller never has to ask whether it knows who the
// message belonged to.
func decodeEnvelope(message []byte) (decoded, error) {
	envelope, err := consume.DecodeEnvelope(message, nexovuev1.Signal_SIGNAL_LOGS)
	if err != nil {
		return decoded{}, err
	}

	var request logsvc.ExportLogsServiceRequest
	if err := proto.Unmarshal(envelope.GetPayload(), &request); err != nil {
		return decoded{}, consume.RejectedBy(envelope, fmt.Errorf("decode OTLP payload: %w", err))
	}
	return decoded{envelope: envelope, request: &request}, nil
}

// stamps are the values every row of one message shares: our clock, and how
// long the project keeps its data.
type stamps struct {
	receivedAt    time.Time
	retentionDays uint16
}

// rows flattens the decoded payload, which by this point has already been
// through the mask stage.
func (d decoded) rows(marks stamps) []logRow {
	rows := make([]logRow, 0, countRecords(d.request))
	for _, resourceLogs := range d.request.GetResourceLogs() {
		resourceAttrs := resourceLogs.GetResource().GetAttributes()
		shared := resourceFacts{
			attrsJSON:   attributesJSON(resourceAttrs),
			serviceName: stringAttribute(resourceAttrs, attrServiceName),
		}
		for _, scopeLogs := range resourceLogs.GetScopeLogs() {
			for _, record := range scopeLogs.GetLogRecords() {
				if record == nil {
					continue
				}
				rows = append(rows, flattenRecord(d.envelope, shared, marks, record))
			}
		}
	}
	return rows
}

// resourceFacts are the values every record under one resource shares, computed
// once rather than per record.
type resourceFacts struct {
	attrsJSON   string
	serviceName string
}

func flattenRecord(
	envelope *nexovuev1.Envelope,
	resource resourceFacts,
	marks stamps,
	record *logspb.LogRecord,
) logRow {
	attributes := record.GetAttributes()
	timestamp, source := recordTime(record, marks.receivedAt)
	severity, severityNumber := severityOf(record)
	body, structured := renderBody(record.GetBody())

	return logRow{
		Timestamp:        timestamp,
		ReceivedAt:       marks.receivedAt,
		RetentionDays:    marks.retentionDays,
		TenantID:         envelope.GetTenantId(),
		ProjectID:        envelope.GetProjectId(),
		TraceID:          hexID(record.GetTraceId(), traceIDHexLen),
		SpanID:           hexID(record.GetSpanId(), spanIDHexLen),
		ServiceName:      resource.serviceName,
		Severity:         severity,
		SeverityNumber:   severityNumber,
		Body:             body,
		BodyStructured:   structured,
		ResourceAttrs:    resource.attrsJSON,
		LogAttrs:         attributesJSON(attributes),
		ExceptionType:    stringAttribute(attributes, attrExceptionType),
		ExceptionMessage: stringAttribute(attributes, attrExceptionMessage),
		TimestampSource:  source,
	}
}

// recordTime picks the clock the row is stored on: the event's own time, else
// the time the collection system observed it, else our receive time.
//
// It never returns zero. A zero timestamp partitions to 1970 and the table's
// TTL deletes the row on arrival, so a record with no clock at all is stored on
// ours and says so.
func recordTime(record *logspb.LogRecord, receivedAt time.Time) (time.Time, string) {
	if event, ok := unixNanoTime(record.GetTimeUnixNano()); ok {
		return event, timestampSourceEvent
	}
	if observed, ok := unixNanoTime(record.GetObservedTimeUnixNano()); ok {
		return observed, timestampSourceObserved
	}
	return receivedAt, timestampSourceReceived
}

// renderBody splits an OTLP body into the two columns ADR 0062 defined: the
// text the index searches, and the typed map when the body was one.
//
// A structured logger emits a map, and flattening that to text would throw away
// the fields the product filters on. Every other shape — number, boolean,
// array, bytes — keeps its JSON text in `body` and an empty document beside it,
// because ClickHouse's JSON type only accepts objects.
func renderBody(body *commonpb.AnyValue) (text, structured string) {
	if body == nil || body.GetValue() == nil {
		return "", emptyJSONObject
	}
	if str, isString := body.GetValue().(*commonpb.AnyValue_StringValue); isString {
		return str.StringValue, emptyJSONObject
	}
	if kvlist, isMap := body.GetValue().(*commonpb.AnyValue_KvlistValue); isMap {
		rendered := jsonText(attributesMap(kvlist.KvlistValue.GetValues()))
		if rendered == "" {
			return "", emptyJSONObject
		}
		return rendered, rendered
	}
	return jsonText(anyValue(body)), emptyJSONObject
}

// unixNanoTime converts an OTLP timestamp to a Go time, reporting whether there
// was one at all. OTLP counts nanoseconds unsigned and Go counts them signed,
// so a value past the signed range is not a real instant: it reads as absent
// rather than wrapping into the distant past.
func unixNanoTime(unixNano uint64) (time.Time, bool) {
	if unixNano == 0 || unixNano > math.MaxInt64 {
		return time.Time{}, false
	}
	return time.Unix(0, int64(unixNano)).UTC(), true
}

// hexID hex-encodes a binary id into exactly width bytes. A record with no
// trace context becomes the FixedString empty value — zero bytes, not null. A
// malformed id is padded or truncated rather than costing us the whole row.
func hexID(id []byte, width int) []byte {
	padded := make([]byte, width)
	encoded := make([]byte, hex.EncodedLen(len(id)))
	hex.Encode(encoded, id)
	copy(padded, encoded)
	return padded
}

func countRecords(request *logsvc.ExportLogsServiceRequest) int {
	total := 0
	for _, resourceLogs := range request.GetResourceLogs() {
		for _, scopeLogs := range resourceLogs.GetScopeLogs() {
			total += len(scopeLogs.GetLogRecords())
		}
	}
	return total
}
