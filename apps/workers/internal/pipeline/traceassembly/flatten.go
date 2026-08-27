// Package traceassembly runs the trace-assembly pipeline: it consumes span
// envelopes from Kafka, flattens them to rows and writes them to ClickHouse.
//
// M1 writes spans through as they arrive rather than buffering a trace in
// memory. The buffer described in ADR 0009 exists to serve tail sampling, which
// is M2 work; until then it would add latency against the span-to-queryable
// budget and buy nothing. See ADR 0054.
package traceassembly

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"time"

	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// Widths of the hex-encoded id columns, which are FixedString in ClickHouse and
// so must be written at exactly their declared size.
const (
	traceIDHexLen = 32
	spanIDHexLen  = 16
)

// attrServiceName and attrServiceVersion are the resource attributes behind two
// promoted columns; service.version is the join key to deploy events.
const (
	attrServiceName    = "service.name"
	attrServiceVersion = "service.version"
)

// nanosPerMillisecond converts OTLP's nanosecond timestamps to the duration
// column, which is milliseconds.
const nanosPerMillisecond = 1e6

// spanRow is one row of the `spans` table, in the order the columns are
// declared. Ids arrive hex-encoded and padded, ready for FixedString.
type spanRow struct {
	Timestamp         time.Time
	TenantID          string
	ProjectID         string
	TraceID           []byte
	SpanID            []byte
	ParentSpanID      []byte
	ServiceName       string
	SpanName          string
	SpanKind          string
	DurationMS        float64
	StatusCode        string
	StatusMessage     string
	HTTPMethod        string
	HTTPRoute         string
	HTTPStatus        uint16
	DeploymentVersion string
	ResourceAttrs     string
	SpanAttrs         string
	Events            string
}

// decoded is one Kafka message unpacked: our envelope and the OTLP request it
// carries, still in protobuf form.
//
// Decoding stops here rather than continuing into rows so the mask stage can
// run on the decoded payload. Masking the payload rather than the rows is what
// guarantees an unmasked value cannot reach a counter or a sampled write — the
// stages after it never see the original at all.
type decoded struct {
	envelope *nexovuev1.Envelope
	request  *tracesvc.ExportTraceServiceRequest
}

// decodeEnvelope unpacks one Kafka message.
func decodeEnvelope(message []byte) (decoded, error) {
	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(message, &envelope); err != nil {
		return decoded{}, fmt.Errorf("decode envelope: %w", err)
	}
	if envelope.GetSignal() != nexovuev1.Signal_SIGNAL_SPANS {
		return decoded{}, fmt.Errorf("envelope carries signal %s, want %s",
			envelope.GetSignal(), nexovuev1.Signal_SIGNAL_SPANS)
	}

	var request tracesvc.ExportTraceServiceRequest
	if err := proto.Unmarshal(envelope.GetPayload(), &request); err != nil {
		return decoded{}, fmt.Errorf("decode OTLP payload: %w", err)
	}
	return decoded{envelope: &envelope, request: &request}, nil
}

// rows flattens the decoded payload, which by this point has already been
// through the mask stage.
func (d decoded) rows() []spanRow { return flattenRequest(d.envelope, d.request) }

func flattenRequest(
	envelope *nexovuev1.Envelope,
	request *tracesvc.ExportTraceServiceRequest,
) []spanRow {
	httpNames := httpNamesFor(envelope.GetSemconvVersion())

	rows := make([]spanRow, 0, countSpans(request))
	for _, resourceSpans := range request.GetResourceSpans() {
		resourceAttrs := resourceSpans.GetResource().GetAttributes()
		shared := resourceFacts{
			attrsJSON:   attributesJSON(resourceAttrs),
			serviceName: attributeOrEmpty(resourceAttrs, attrServiceName),
			version:     attributeOrEmpty(resourceAttrs, attrServiceVersion),
		}
		for _, scopeSpans := range resourceSpans.GetScopeSpans() {
			for _, span := range scopeSpans.GetSpans() {
				if span == nil {
					continue
				}
				rows = append(rows, flattenSpan(envelope, shared, httpNames, span))
			}
		}
	}
	return rows
}

// resourceFacts are the values every span under one resource shares, computed
// once rather than per span.
type resourceFacts struct {
	attrsJSON   string
	serviceName string
	version     string
}

func flattenSpan(
	envelope *nexovuev1.Envelope,
	resource resourceFacts,
	httpNames [2]httpAttributeNames,
	span *tracepb.Span,
) spanRow {
	attributes := span.GetAttributes()
	promoted := promotedHTTP(attributes, httpNames)

	return spanRow{
		Timestamp:         unixNanoTime(span.GetStartTimeUnixNano()),
		TenantID:          envelope.GetTenantId(),
		ProjectID:         envelope.GetProjectId(),
		TraceID:           hexID(span.GetTraceId(), traceIDHexLen),
		SpanID:            hexID(span.GetSpanId(), spanIDHexLen),
		ParentSpanID:      hexID(span.GetParentSpanId(), spanIDHexLen),
		ServiceName:       resource.serviceName,
		SpanName:          span.GetName(),
		SpanKind:          spanKindName(span.GetKind()),
		DurationMS:        durationMS(span.GetStartTimeUnixNano(), span.GetEndTimeUnixNano()),
		StatusCode:        statusCodeName(span.GetStatus().GetCode()),
		StatusMessage:     span.GetStatus().GetMessage(),
		HTTPMethod:        promoted.method,
		HTTPRoute:         promoted.route,
		HTTPStatus:        promoted.status,
		DeploymentVersion: resource.version,
		ResourceAttrs:     resource.attrsJSON,
		SpanAttrs:         attributesJSON(attributes),
		Events:            eventsJSON(span.GetEvents()),
	}
}

// promotedValues are the HTTP columns lifted out of the attribute map.
type promotedValues struct {
	method string
	route  string
	status uint16
}

// promotedHTTP reads the promoted columns, trying each convention generation in
// the order the envelope's semconv_version put them in and taking the first
// spelling that is present.
func promotedHTTP(attributes []*commonpb.KeyValue, names [2]httpAttributeNames) promotedValues {
	var promoted promotedValues
	for _, generation := range names {
		if promoted.method == "" {
			promoted.method, _ = stringAttribute(attributes, generation.method)
		}
		if promoted.route == "" {
			promoted.route, _ = stringAttribute(attributes, generation.route)
		}
		if promoted.status == 0 {
			if status, ok := intAttribute(attributes, generation.status); ok {
				promoted.status = clampStatus(status)
			}
		}
	}
	return promoted
}

// clampStatus keeps a nonsensical status out of the UInt16 column, where it
// would otherwise wrap into a plausible-looking code.
func clampStatus(status int64) uint16 {
	if status < 0 || status > int64(^uint16(0)) {
		return 0
	}
	return uint16(status)
}

// spanEvent is the shape serialized into the `events` String column.
type spanEvent struct {
	Timestamp  string         `json:"timestamp"`
	Name       string         `json:"name"`
	Attributes map[string]any `json:"attributes"`
}

// eventsJSON serializes span events as a JSON array. The column is a String
// rather than JSON because events are read whole with a span, never filtered on.
func eventsJSON(events []*tracepb.Span_Event) string {
	if len(events) == 0 {
		return "[]"
	}
	rendered := make([]spanEvent, 0, len(events))
	for _, event := range events {
		if event == nil {
			continue
		}
		rendered = append(rendered, spanEvent{
			Timestamp:  unixNanoTime(event.GetTimeUnixNano()).Format(time.RFC3339Nano),
			Name:       event.GetName(),
			Attributes: attributesMap(event.GetAttributes()),
		})
	}
	encoded, err := json.Marshal(rendered)
	if err != nil {
		return "[]"
	}
	return string(encoded)
}

// unixNanoTime converts an OTLP timestamp to a Go time. OTLP counts nanoseconds
// unsigned and Go counts them signed, so a value past the signed range is not a
// real instant: it becomes the epoch rather than wrapping into the distant past.
func unixNanoTime(unixNano uint64) time.Time {
	if unixNano > math.MaxInt64 {
		return time.Unix(0, 0).UTC()
	}
	return time.Unix(0, int64(unixNano)).UTC()
}

// durationMS is the span's wall time in milliseconds. The OTLP timestamps are
// unsigned, so an end before its start would wrap into an enormous duration;
// that reads as zero instead, which is visibly wrong rather than plausibly so.
func durationMS(startUnixNano, endUnixNano uint64) float64 {
	if endUnixNano < startUnixNano {
		return 0
	}
	return float64(endUnixNano-startUnixNano) / nanosPerMillisecond
}

// hexID hex-encodes a binary id into exactly width bytes. A root span's parent
// is absent, which becomes the FixedString empty value — zero bytes, not null.
// A malformed id is padded or truncated rather than costing us the whole row.
func hexID(id []byte, width int) []byte {
	padded := make([]byte, width)
	encoded := make([]byte, hex.EncodedLen(len(id)))
	hex.Encode(encoded, id)
	copy(padded, encoded)
	return padded
}

func spanKindName(kind tracepb.Span_SpanKind) string {
	return strings.TrimPrefix(kind.String(), "SPAN_KIND_")
}

func statusCodeName(code tracepb.Status_StatusCode) string {
	return strings.TrimPrefix(code.String(), "STATUS_CODE_")
}

func attributeOrEmpty(attributes []*commonpb.KeyValue, key string) string {
	value, _ := stringAttribute(attributes, key)
	return value
}

func countSpans(request *tracesvc.ExportTraceServiceRequest) int {
	total := 0
	for _, resourceSpans := range request.GetResourceSpans() {
		for _, scopeSpans := range resourceSpans.GetScopeSpans() {
			total += len(scopeSpans.GetSpans())
		}
	}
	return total
}

// millisecondsDuration converts a duration column back to a Go duration, which
// is what the sampler compares against its threshold.
func millisecondsDuration(durationMS float64) time.Duration {
	if durationMS <= 0 {
		return 0
	}
	return time.Duration(durationMS * float64(time.Millisecond))
}
