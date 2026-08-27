package otlp

import (
	"encoding/json"
	"testing"

	"google.golang.org/protobuf/encoding/protojson"

	tracepb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
)

// The regression: the receiver decoded OTLP/JSON with plain protojson, which
// reads `bytes` as base64. Every spec-compliant exporter sends hex, so every one
// of them was answered with 400.
func TestSpecCompliantHexJSONIsAccepted(t *testing.T) {
	const traceID = "4bf92f3577b34da6a3ce929d0e0e4736"
	const spanID = "00f067aa0ba902b7"
	const parentID = "0102030405060708"

	body := []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{
		"traceId":"` + traceID + `","spanId":"` + spanID + `","parentSpanId":"` + parentID + `",
		"name":"GET /checkout",
		"links":[{"traceId":"` + traceID + `","spanId":"` + spanID + `"}]}]}]}]}`)

	var request tracepb.ExportTraceServiceRequest
	if err := jsonCodec.unmarshal(body, &request); err != nil {
		t.Fatalf("spec-compliant OTLP/JSON was rejected: %v", err)
	}

	span := request.GetResourceSpans()[0].GetScopeSpans()[0].GetSpans()[0]
	if got := len(span.GetTraceId()); got != 16 {
		t.Errorf("trace id decoded to %d bytes, want 16 — hex was read as base64", got)
	}
	if got := len(span.GetSpanId()); got != 8 {
		t.Errorf("span id decoded to %d bytes, want 8", got)
	}
	if got := len(span.GetParentSpanId()); got != 8 {
		t.Errorf("parent span id decoded to %d bytes, want 8", got)
	}
	// Links carry ids too, and a walk that only knew about spans would miss them.
	link := span.GetLinks()[0]
	if got := len(link.GetTraceId()); got != 16 {
		t.Errorf("link trace id decoded to %d bytes, want 16", got)
	}
}

// A client that sends protobuf's own JSON mapping must keep working: the two
// encodings are told apart by exact hex width, not by guessing.
func TestBase64JSONStillAccepted(t *testing.T) {
	raw := []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{
		"traceId":"S/kvNXezTaajzpKdDg5HNg==","spanId":"APBnqgupArc=","name":"x"}]}]}]}`)

	var request tracepb.ExportTraceServiceRequest
	if err := jsonCodec.unmarshal(raw, &request); err != nil {
		t.Fatalf("base64 OTLP/JSON was rejected: %v", err)
	}
	if got := len(request.GetResourceSpans()[0].GetScopeSpans()[0].GetSpans()[0].GetTraceId()); got != 16 {
		t.Errorf("trace id decoded to %d bytes, want 16", got)
	}
}

func TestSnakeCaseFieldNamesAreHandled(t *testing.T) {
	raw := []byte(`{"resource_spans":[{"scope_spans":[{"spans":[{
		"trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"00f067aa0ba902b7","name":"x"}]}]}]}`)

	var request tracepb.ExportTraceServiceRequest
	if err := jsonCodec.unmarshal(raw, &request); err != nil {
		t.Fatalf("snake_case OTLP/JSON was rejected: %v", err)
	}
	if got := len(request.GetResourceSpans()[0].GetScopeSpans()[0].GetSpans()[0].GetTraceId()); got != 16 {
		t.Errorf("trace id decoded to %d bytes, want 16", got)
	}
}

func TestNonIDFieldsAreUntouched(t *testing.T) {
	// `name` happens to be 32 hex characters. It is not an id field, so it must
	// survive verbatim rather than being rewritten by a blind value scan.
	raw := []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"4bf92f3577b34da6a3ce929d0e0e4736"}]}]}]}`)

	normalized, err := normalizeJSONIDs(raw)
	if err != nil {
		t.Fatalf("normalize: %v", err)
	}
	var document map[string]any
	if err := json.Unmarshal(normalized, &document); err != nil {
		t.Fatalf("result is not JSON: %v", err)
	}
	var request tracepb.ExportTraceServiceRequest
	if err := protojson.Unmarshal(normalized, &request); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got := request.GetResourceSpans()[0].GetScopeSpans()[0].GetSpans()[0].GetName(); got != "4bf92f3577b34da6a3ce929d0e0e4736" {
		t.Errorf("name = %q, want it unchanged", got)
	}
}

func TestMalformedJSONStillReportsOneError(t *testing.T) {
	var request tracepb.ExportTraceServiceRequest
	if err := jsonCodec.unmarshal([]byte(`{"resourceSpans":`), &request); err == nil {
		t.Fatal("malformed JSON was accepted")
	}
}
