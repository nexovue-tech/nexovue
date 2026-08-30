package traceassembly

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"

	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// Ids as OTLP carries them: 16 raw bytes for a trace, 8 for a span.
var (
	testTraceID = []byte{0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
		0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10}
	testSpanID   = []byte{0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18}
	testParentID = []byte{0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28}
)

func stringValue(value string) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}}
}

func intValue(value int64) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: value}}
}

func attribute(key string, value *commonpb.AnyValue) *commonpb.KeyValue {
	return &commonpb.KeyValue{Key: key, Value: value}
}

// spanOptions describe the one span a test payload carries.
type spanOptions struct {
	attributes   []*commonpb.KeyValue
	parentSpanID []byte
	kind         tracepb.Span_SpanKind
	status       *tracepb.Status
	events       []*tracepb.Span_Event
	startNano    uint64
	endNano      uint64
}

// marshalEnvelope builds the bytes a Kafka message would carry.
func marshalEnvelope(t *testing.T, semconv string, options spanOptions) []byte {
	t.Helper()

	if options.startNano == 0 {
		options.startNano = 1_700_000_000_000_000_000
	}
	if options.endNano == 0 {
		options.endNano = options.startNano + 1_500_000 // 1.5 ms
	}

	request := &tracesvc.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{{
			Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
				attribute(attrServiceName, stringValue("checkout")),
				attribute(attrServiceVersion, stringValue("2026.08.1")),
			}},
			ScopeSpans: []*tracepb.ScopeSpans{{
				Spans: []*tracepb.Span{{
					TraceId:           testTraceID,
					SpanId:            testSpanID,
					ParentSpanId:      options.parentSpanID,
					Name:              "GET /orders/:id",
					Kind:              options.kind,
					StartTimeUnixNano: options.startNano,
					EndTimeUnixNano:   options.endNano,
					Attributes:        options.attributes,
					Events:            options.events,
					Status:            options.status,
				}},
			}},
		}},
	}
	payload, err := proto.Marshal(request)
	if err != nil {
		t.Fatalf("marshalling the OTLP request: %v", err)
	}

	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:       "tenant-a",
		ProjectId:      "project-a",
		Door:           nexovuev1.Door_DOOR_OTLP,
		Signal:         nexovuev1.Signal_SIGNAL_SPANS,
		SemconvVersion: semconv,
		Payload:        payload,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	return message
}

// flattenEnvelope decodes a message and flattens it in one step, which is what
// the pipeline does either side of the mask stage.
func flattenEnvelope(message []byte) ([]spanRow, error) {
	payload, err := decodeEnvelope(message)
	if err != nil {
		return nil, err
	}
	return payload.rows(), nil
}

// flattenOne decodes an envelope that is known to carry exactly one span.
func flattenOne(t *testing.T, message []byte) spanRow {
	t.Helper()
	rows, err := flattenEnvelope(message)
	if err != nil {
		t.Fatalf("flattenEnvelope() = %v, want no error", err)
	}
	if len(rows) != 1 {
		t.Fatalf("flattenEnvelope() returned %d rows, want 1", len(rows))
	}
	return rows[0]
}

// The whole reason the envelope carries semconv_version: customers run SDKs
// from both sides of the HTTP stabilization, and both must fill the same
// promoted columns.
func TestPromotedHTTPColumnsAreTheSameAcrossConventions(t *testing.T) {
	cases := map[string]struct {
		semconv    string
		attributes []*commonpb.KeyValue
	}{
		"stable names on a stable version": {
			semconv: "1.27.0",
			attributes: []*commonpb.KeyValue{
				attribute(attrHTTPMethodStable, stringValue("GET")),
				attribute(attrHTTPRoute, stringValue("/orders/:id")),
				attribute(attrHTTPStatusStable, intValue(200)),
			},
		},
		"pre-stable names on a pre-stable version": {
			semconv: "1.21.0",
			attributes: []*commonpb.KeyValue{
				attribute(attrHTTPMethodLegacy, stringValue("GET")),
				attribute(attrHTTPRoute, stringValue("/orders/:id")),
				attribute(attrHTTPStatusLegacy, intValue(200)),
			},
		},
		"pre-stable names on a stable version": {
			semconv: "1.27.0",
			attributes: []*commonpb.KeyValue{
				attribute(attrHTTPMethodLegacy, stringValue("GET")),
				attribute(attrHTTPRoute, stringValue("/orders/:id")),
				attribute(attrHTTPStatusLegacy, intValue(200)),
			},
		},
		"stable names on a pre-stable version": {
			semconv: "1.21.0",
			attributes: []*commonpb.KeyValue{
				attribute(attrHTTPMethodStable, stringValue("GET")),
				attribute(attrHTTPRoute, stringValue("/orders/:id")),
				attribute(attrHTTPStatusStable, intValue(200)),
			},
		},
		"no version declared at all": {
			semconv: "",
			attributes: []*commonpb.KeyValue{
				attribute(attrHTTPMethodLegacy, stringValue("GET")),
				attribute(attrHTTPRoute, stringValue("/orders/:id")),
				attribute(attrHTTPStatusLegacy, intValue(200)),
			},
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			row := flattenOne(t, marshalEnvelope(t, tc.semconv, spanOptions{
				attributes: tc.attributes,
			}))

			if row.HTTPMethod != "GET" {
				t.Errorf("HTTPMethod = %q, want %q", row.HTTPMethod, "GET")
			}
			if row.HTTPRoute != "/orders/:id" {
				t.Errorf("HTTPRoute = %q, want %q", row.HTTPRoute, "/orders/:id")
			}
			if row.HTTPStatus != 200 {
				t.Errorf("HTTPStatus = %d, want 200", row.HTTPStatus)
			}
		})
	}
}

// An SDK migrating with OTEL_SEMCONV_STABILITY_OPT_IN=http/dup emits both
// spellings at once, and then the declared version decides which one wins.
func TestSemconvVersionBreaksTheTieWhenBothSpellingsArePresent(t *testing.T) {
	both := []*commonpb.KeyValue{
		attribute(attrHTTPMethodStable, stringValue("GET")),
		attribute(attrHTTPMethodLegacy, stringValue("get")),
		attribute(attrHTTPStatusStable, intValue(200)),
		attribute(attrHTTPStatusLegacy, intValue(500)),
	}

	cases := map[string]struct {
		semconv    string
		wantMethod string
		wantStatus uint16
	}{
		"stable version prefers the stable spelling":     {"1.23.0", "GET", 200},
		"pre-stable version prefers the older spelling":  {"1.22.0", "get", 500},
		"unparseable version falls back to stable first": {"not-a-version", "GET", 200},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			row := flattenOne(t, marshalEnvelope(t, tc.semconv, spanOptions{attributes: both}))

			if row.HTTPMethod != tc.wantMethod {
				t.Errorf("HTTPMethod = %q, want %q", row.HTTPMethod, tc.wantMethod)
			}
			if row.HTTPStatus != tc.wantStatus {
				t.Errorf("HTTPStatus = %d, want %d", row.HTTPStatus, tc.wantStatus)
			}
		})
	}
}

func TestFlattenFillsTheIdentityColumns(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		parentSpanID: testParentID,
		kind:         tracepb.Span_SPAN_KIND_SERVER,
		status: &tracepb.Status{
			Code:    tracepb.Status_STATUS_CODE_ERROR,
			Message: "upstream refused",
		},
	}))

	checks := map[string]struct{ got, want string }{
		"tenant_id":      {row.TenantID, "tenant-a"},
		"project_id":     {row.ProjectID, "project-a"},
		"trace_id":       {string(row.TraceID), "0102030405060708090a0b0c0d0e0f10"},
		"span_id":        {string(row.SpanID), "1112131415161718"},
		"parent_span_id": {string(row.ParentSpanID), "2122232425262728"},
		"service_name":   {row.ServiceName, "checkout"},
		"span_name":      {row.SpanName, "GET /orders/:id"},
		"span_kind":      {row.SpanKind, "SERVER"},
		"status_code":    {row.StatusCode, "ERROR"},
		"status_message": {row.StatusMessage, "upstream refused"},
		// service.version is the join key to deploy events.
		"deployment_version": {row.DeploymentVersion, "2026.08.1"},
	}
	for column, check := range checks {
		if check.got != check.want {
			t.Errorf("%s = %q, want %q", column, check.got, check.want)
		}
	}

	if want := time.Unix(0, 1_700_000_000_000_000_000).UTC(); !row.Timestamp.Equal(want) {
		t.Errorf("Timestamp = %s, want %s", row.Timestamp, want)
	}
	if row.DurationMS != 1.5 {
		t.Errorf("DurationMS = %v, want 1.5", row.DurationMS)
	}
}

// FixedString(16) has no null: a root span's parent is the empty value, which
// is zero bytes, and that is what `parent_span_id = ”` matches in ClickHouse.
func TestRootSpanParentIsTheEmptyFixedStringValue(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{parentSpanID: nil}))

	if len(row.ParentSpanID) != otlpvalue.SpanIDHexLen {
		t.Fatalf("len(ParentSpanID) = %d, want %d", len(row.ParentSpanID), otlpvalue.SpanIDHexLen)
	}
	for i, b := range row.ParentSpanID {
		if b != 0 {
			t.Fatalf("ParentSpanID[%d] = %#x, want a zero byte throughout", i, b)
		}
	}
}

// Every id column is FixedString, and ch-go panics on a value of the wrong
// width, so the widths are guaranteed here rather than discovered in flight.
func TestIDColumnsAreAlwaysExactlyTheirColumnWidth(t *testing.T) {
	cases := map[string]struct {
		id    []byte
		width int
	}{
		"absent":              {nil, otlpvalue.SpanIDHexLen},
		"correct length":      {testSpanID, otlpvalue.SpanIDHexLen},
		"shorter than a span": {[]byte{0x01, 0x02}, otlpvalue.SpanIDHexLen},
		"longer than a trace": {append(append([]byte{}, testTraceID...), testTraceID...), otlpvalue.TraceIDHexLen},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := otlpvalue.HexID(tc.id, tc.width); len(got) != tc.width {
				t.Errorf("len(otlpvalue.HexID(...)) = %d, want %d", len(got), tc.width)
			}
		})
	}
}

func TestFlattenSerializesAttributesAndEvents(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		attributes: []*commonpb.KeyValue{attribute("db.system", stringValue("postgresql"))},
		events: []*tracepb.Span_Event{{
			TimeUnixNano: 1_700_000_000_000_000_000,
			Name:         "exception",
			Attributes: []*commonpb.KeyValue{
				attribute("exception.type", stringValue("TimeoutError")),
			},
		}},
	}))

	var resource, span map[string]any
	if err := json.Unmarshal([]byte(row.ResourceAttrs), &resource); err != nil {
		t.Fatalf("resource_attrs is not JSON: %v (%q)", err, row.ResourceAttrs)
	}
	if otlpvalue.AttributeAt(resource, attrServiceName) != "checkout" {
		t.Errorf("resource_attrs[%s] = %v, want checkout", attrServiceName, otlpvalue.AttributeAt(resource, attrServiceName))
	}
	if err := json.Unmarshal([]byte(row.SpanAttrs), &span); err != nil {
		t.Fatalf("span_attrs is not JSON: %v (%q)", err, row.SpanAttrs)
	}
	if otlpvalue.AttributeAt(span, "db.system") != "postgresql" {
		t.Errorf("span_attrs[db.system] = %v, want postgresql", otlpvalue.AttributeAt(span, "db.system"))
	}

	var events []spanEvent
	if err := json.Unmarshal([]byte(row.Events), &events); err != nil {
		t.Fatalf("events is not JSON: %v (%q)", err, row.Events)
	}
	if len(events) != 1 || events[0].Name != "exception" {
		t.Fatalf("events = %+v, want one exception event", events)
	}
	if otlpvalue.AttributeAt(events[0].Attributes, "exception.type") != "TimeoutError" {
		t.Errorf("event attributes = %v, want exception.type TimeoutError", events[0].Attributes)
	}
}

// The JSON columns are not nullable, so a span with nothing on it must still
// produce parsable documents.
func TestEmptyAttributesAndEventsStillProduceValidJSON(t *testing.T) {
	rows, err := flattenEnvelope(marshalEnvelope(t, "1.27.0", spanOptions{}))
	if err != nil {
		t.Fatalf("flattenEnvelope() = %v, want no error", err)
	}
	if got := rows[0].SpanAttrs; got != otlpvalue.EmptyObject {
		t.Errorf("SpanAttrs = %q, want %q", got, otlpvalue.EmptyObject)
	}
	if got := rows[0].Events; got != "[]" {
		t.Errorf("Events = %q, want []", got)
	}
}

func TestFlattenRejectsMessagesItMustNotWrite(t *testing.T) {
	logsEnvelope, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId: "tenant-a",
		Signal:   nexovuev1.Signal_SIGNAL_LOGS,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	spansWithJunkPayload, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId: "tenant-a",
		Signal:   nexovuev1.Signal_SIGNAL_SPANS,
		Payload:  []byte{0xff, 0xfe, 0xfd, 0xfc},
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}

	cases := map[string]struct {
		message   []byte
		wantInErr string
	}{
		"not an envelope at all":        {[]byte{0xff, 0xfe, 0xfd, 0xfc}, "decode envelope"},
		"envelope for the wrong signal": {logsEnvelope, "signal"},
		"envelope with a junk payload":  {spansWithJunkPayload, "decode OTLP payload"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := flattenEnvelope(tc.message)
			if err == nil {
				t.Fatal("flattenEnvelope() succeeded, want an error")
			}
			if !strings.Contains(err.Error(), tc.wantInErr) {
				t.Errorf("flattenEnvelope() = %q, want it to mention %q", err, tc.wantInErr)
			}
		})
	}
}

func TestDurationIsZeroRatherThanWrappingWhenAnEndPrecedesItsStart(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		startNano: 1_700_000_000_000_000_000,
		endNano:   1_699_999_999_000_000_000,
	}))
	if row.DurationMS != 0 {
		t.Errorf("DurationMS = %v, want 0 for an end before its start", row.DurationMS)
	}
}

func TestStatusOutsideTheColumnRangeBecomesZero(t *testing.T) {
	cases := map[string]struct {
		status int64
		want   uint16
	}{
		"ordinary":   {404, 404},
		"negative":   {-1, 0},
		"too large":  {70000, 0},
		"at the top": {65535, 65535},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := clampStatus(tc.status); got != tc.want {
				t.Errorf("clampStatus(%d) = %d, want %d", tc.status, got, tc.want)
			}
		})
	}
}

func TestOneRequestProducesOneRowPerSpanAcrossEveryScope(t *testing.T) {
	span := func(name string) *tracepb.Span {
		return &tracepb.Span{TraceId: testTraceID, SpanId: testSpanID, Name: name}
	}
	request := &tracesvc.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{
			{ScopeSpans: []*tracepb.ScopeSpans{
				{Spans: []*tracepb.Span{span("a"), span("b")}},
				{Spans: []*tracepb.Span{span("c")}},
			}},
			{ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span("d")}}}},
		},
	}
	payload, err := proto.Marshal(request)
	if err != nil {
		t.Fatalf("marshalling the OTLP request: %v", err)
	}
	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId: "tenant-a",
		Signal:   nexovuev1.Signal_SIGNAL_SPANS,
		Payload:  payload,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}

	rows, err := flattenEnvelope(message)
	if err != nil {
		t.Fatalf("flattenEnvelope() = %v, want no error", err)
	}
	if len(rows) != 4 {
		t.Fatalf("flattenEnvelope() returned %d rows, want 4", len(rows))
	}
}

// OTLP timestamps are unsigned and Go's are not, so a value past the signed
// range must not wrap into a plausible-looking instant in the distant past.
func TestOutOfRangeTimestampBecomesTheEpoch(t *testing.T) {
	cases := map[string]struct {
		unixNano uint64
		want     time.Time
	}{
		"ordinary":              {1_700_000_000_000_000_000, time.Unix(0, 1_700_000_000_000_000_000).UTC()},
		"zero":                  {0, time.Unix(0, 0).UTC()},
		"past the signed range": {math.MaxUint64, time.Unix(0, 0).UTC()},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := unixNanoTime(tc.unixNano); !got.Equal(tc.want) {
				t.Errorf("unixNanoTime(%d) = %s, want %s", tc.unixNano, got, tc.want)
			}
		})
	}
}

// A consumer's producer is named by a link and by nothing else, so a link that
// does not survive flattening is a queue edge that can never be drawn.
func TestSpanLinksAreFlattened(t *testing.T) {
	linkedTraceID := []byte{0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38,
		0x39, 0x3a, 0x3b, 0x3c, 0x3d, 0x3e, 0x3f, 0x40}
	linkedSpanID := []byte{0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48}

	rows := flattenRequest(
		&nexovuev1.Envelope{TenantId: "tenant-a", ProjectId: "project-a"},
		&tracesvc.ExportTraceServiceRequest{ResourceSpans: []*tracepb.ResourceSpans{{
			ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{{
				TraceId: testTraceID,
				SpanId:  testSpanID,
				Kind:    tracepb.Span_SPAN_KIND_CONSUMER,
				Links: []*tracepb.Span_Link{
					{
						TraceId:    linkedTraceID,
						SpanId:     linkedSpanID,
						Attributes: []*commonpb.KeyValue{attribute("messaging.batch", intValue(3))},
					},
					// Names no span: it cannot be paired and must not be stored
					// as an id of zeroes that reads like a real one.
					{SpanId: linkedSpanID},
				},
			}}}},
		}}},
	)

	if len(rows) != 1 {
		t.Fatalf("expected one row, got %d", len(rows))
	}
	links := rows[0].Links
	if len(links) != 1 {
		t.Fatalf("expected the one usable link, got %d: %+v", len(links), links)
	}
	if got := string(links[0].TraceID); got != "3132333435363738393a3b3c3d3e3f40" {
		t.Errorf("link trace id = %q", got)
	}
	if got := string(links[0].SpanID); got != "4142434445464748" {
		t.Errorf("link span id = %q", got)
	}
	if !strings.Contains(links[0].Attributes, `"batch":3`) {
		t.Errorf("link attributes = %q, want the batch size", links[0].Attributes)
	}
}
