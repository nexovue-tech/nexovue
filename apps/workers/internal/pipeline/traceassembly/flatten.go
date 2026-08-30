package traceassembly

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"

	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
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
	// TraceState is the span's raw W3C tracestate, carried for the sampler and
	// never written: it reads an explicit `rv` from it when a producer upstream
	// has already fixed the trace's randomness, so our decision agrees with
	// theirs instead of being an independent coin flip.
	TraceState string

	Timestamp time.Time
	// ReceivedAt is our own clock. The stored row partitions and expires on it,
	// so a client's wrong clock cannot pin a row in storage or delete it early.
	ReceivedAt time.Time
	// RetentionDays is this row's own expiry, and DerivedRetentionDays is the
	// one the rollups built from it use. Both sit in the partition key of the
	// table they land in, so a part holds one retention and drops whole.
	RetentionDays        uint16
	DerivedRetentionDays uint16
	TenantID             string
	ProjectID            string
	TraceID              []byte
	SpanID               []byte
	ParentSpanID         []byte
	ServiceName          string
	SpanName             string
	SpanKind             string
	DurationMS           float64
	StatusCode           string
	StatusMessage        string
	HTTPMethod           string
	HTTPRoute            string
	HTTPStatus           uint16
	RPCSystem            string
	RPCMethod            string
	RPCStatus            string
	DeploymentVersion    string
	ResourceAttrs        string
	SpanAttrs            string
	Events               string
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

// decodeEnvelope unpacks one Kafka message. Every failure is a
// *consume.Rejection, so the caller never has to ask whether it knows who the
// message belonged to.
func decodeEnvelope(message []byte) (decoded, error) {
	envelope, err := consume.DecodeEnvelope(message, nexovuev1.Signal_SIGNAL_SPANS)
	if err != nil {
		return decoded{}, err
	}

	var request tracesvc.ExportTraceServiceRequest
	if err := proto.Unmarshal(envelope.GetPayload(), &request); err != nil {
		return decoded{}, consume.RejectedBy(envelope, fmt.Errorf("decode OTLP payload: %w", err))
	}
	return decoded{envelope: envelope, request: &request}, nil
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
			attrsJSON:   otlpvalue.AttributesJSON(resourceAttrs),
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
	rpc := promotedRPC(attributes)

	return spanRow{
		Timestamp:         unixNanoTime(span.GetStartTimeUnixNano()),
		TenantID:          envelope.GetTenantId(),
		ProjectID:         envelope.GetProjectId(),
		TraceID:           otlpvalue.HexID(span.GetTraceId(), otlpvalue.TraceIDHexLen),
		SpanID:            otlpvalue.HexID(span.GetSpanId(), otlpvalue.SpanIDHexLen),
		ParentSpanID:      otlpvalue.HexID(span.GetParentSpanId(), otlpvalue.SpanIDHexLen),
		ServiceName:       resource.serviceName,
		SpanName:          span.GetName(),
		SpanKind:          spanKindName(span.GetKind()),
		DurationMS:        durationMS(span.GetStartTimeUnixNano(), span.GetEndTimeUnixNano()),
		StatusCode:        statusCodeName(span.GetStatus().GetCode()),
		StatusMessage:     span.GetStatus().GetMessage(),
		HTTPMethod:        promoted.method,
		HTTPRoute:         promoted.route,
		HTTPStatus:        promoted.status,
		RPCSystem:         rpc.system,
		RPCMethod:         rpc.method,
		RPCStatus:         rpc.status,
		DeploymentVersion: resource.version,
		ResourceAttrs:     resource.attrsJSON,
		SpanAttrs:         otlpvalue.AttributesJSON(attributes),
		Events:            eventsJSON(span.GetEvents()),
		// Carried for the sampler alone, which reads an explicit `rv` out of it
		// when a producer upstream has already fixed the trace's randomness. Not
		// stored: nothing queries it.
		TraceState: span.GetTraceState(),
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
			promoted.method, _ = otlpvalue.String(attributes, generation.method)
		}
		if promoted.route == "" {
			promoted.route, _ = otlpvalue.String(attributes, generation.route)
		}
		if promoted.status == 0 {
			if status, ok := otlpvalue.Int(attributes, generation.status); ok {
				promoted.status = clampStatus(status)
			}
		}
	}
	return promoted
}

// rpcValues are the RPC columns lifted out of the attribute map, already
// normalized onto the current convention's vocabulary.
type rpcValues struct {
	system string
	method string
	status string
}

// promotedRPC reads the RPC columns, taking the current spelling of each where
// it is present and the legacy one otherwise.
func promotedRPC(attributes []*commonpb.KeyValue) rpcValues {
	system := otlpvalue.StringOrEmpty(attributes, attrRPCSystemCurrent)
	if system == "" {
		system = otlpvalue.StringOrEmpty(attributes, attrRPCSystemLegacy)
	}
	return rpcValues{
		system: canonicalRPCSystem(system),
		method: qualifiedRPCMethod(
			otlpvalue.StringOrEmpty(attributes, attrRPCServiceLegacy),
			otlpvalue.StringOrEmpty(attributes, attrRPCMethod),
		),
		status: rpcStatusText(attributes),
	}
}

// rpcStatusText is the call's outcome as the current convention spells it. The
// legacy codes are each read in the type they were defined with — gRPC's is an
// integer over a named enum, Connect RPC's a string, JSON-RPC's a bare number.
func rpcStatusText(attributes []*commonpb.KeyValue) string {
	if status := otlpvalue.StringOrEmpty(attributes, attrRPCStatusCurrent); status != "" {
		return status
	}
	if code, ok := otlpvalue.Int(attributes, attrRPCStatusGRPCLegacy); ok {
		return grpcStatusName(code)
	}
	if code := otlpvalue.StringOrEmpty(attributes, attrRPCStatusConnectLegacy); code != "" {
		return code
	}
	if code, ok := otlpvalue.Int(attributes, attrRPCStatusJSONRPCLegacy); ok {
		return strconv.FormatInt(code, 10)
	}
	return ""
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
			Attributes: otlpvalue.AttributesMap(event.GetAttributes()),
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

func spanKindName(kind tracepb.Span_SpanKind) string {
	return strings.TrimPrefix(kind.String(), "SPAN_KIND_")
}

func statusCodeName(code tracepb.Status_StatusCode) string {
	return strings.TrimPrefix(code.String(), "STATUS_CODE_")
}

func attributeOrEmpty(attributes []*commonpb.KeyValue, key string) string {
	value, _ := otlpvalue.String(attributes, key)
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
