package traceassembly

import (
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
)

func TestAttributesJSONRendersEveryOTLPValueShape(t *testing.T) {
	cases := map[string]struct {
		value *commonpb.AnyValue
		want  string
	}{
		"string": {stringValue("GET"), `{"k":"GET"}`},
		"bool": {&commonpb.AnyValue{
			Value: &commonpb.AnyValue_BoolValue{BoolValue: true},
		}, `{"k":true}`},
		"int": {intValue(7), `{"k":7}`},
		"double": {&commonpb.AnyValue{
			Value: &commonpb.AnyValue_DoubleValue{DoubleValue: 1.5},
		}, `{"k":1.5}`},
		"bytes become base64": {&commonpb.AnyValue{
			Value: &commonpb.AnyValue_BytesValue{BytesValue: []byte{0x01, 0x02}},
		}, `{"k":"AQI="}`},
		"array": {&commonpb.AnyValue{Value: &commonpb.AnyValue_ArrayValue{
			ArrayValue: &commonpb.ArrayValue{
				Values: []*commonpb.AnyValue{stringValue("a"), intValue(2)},
			},
		}}, `{"k":["a",2]}`},
		"nested key value list": {&commonpb.AnyValue{Value: &commonpb.AnyValue_KvlistValue{
			KvlistValue: &commonpb.KeyValueList{
				Values: []*commonpb.KeyValue{attribute("inner", stringValue("v"))},
			},
		}}, `{"k":{"inner":"v"}}`},
		// An unset value keeps its key so the gap is visible in a query.
		"unset": {nil, `{"k":null}`},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got := otlpvalue.AttributesJSON([]*commonpb.KeyValue{attribute("k", tc.value)})
			if got != tc.want {
				t.Errorf("otlpvalue.AttributesJSON() = %s, want %s", got, tc.want)
			}
		})
	}
}

func TestAttributesJSONWithNothingToRenderIsAnEmptyObject(t *testing.T) {
	if got := otlpvalue.AttributesJSON(nil); got != otlpvalue.EmptyObject {
		t.Errorf("otlpvalue.AttributesJSON(nil) = %s, want %s", got, otlpvalue.EmptyObject)
	}
}

// A key-less attribute would become a JSON path of "", which ClickHouse cannot
// make a subcolumn of.
func TestAttributesWithoutAKeyAreDropped(t *testing.T) {
	attributes := []*commonpb.KeyValue{
		attribute("", stringValue("orphan")),
		attribute("kept", stringValue("v")),
	}
	if got := otlpvalue.AttributesJSON(attributes); got != `{"kept":"v"}` {
		t.Errorf("otlpvalue.AttributesJSON() = %s, want {\"kept\":\"v\"}", got)
	}
}

func TestPromotedLookupsIgnoreValuesOfTheWrongType(t *testing.T) {
	attributes := []*commonpb.KeyValue{
		attribute("as.int", intValue(5)),
		attribute("as.string", stringValue("5")),
		attribute("as.double", &commonpb.AnyValue{
			Value: &commonpb.AnyValue_DoubleValue{DoubleValue: 503},
		}),
	}

	if _, ok := otlpvalue.String(attributes, "as.int"); ok {
		t.Error("stringAttribute accepted an int; a promoted string column would hold nonsense")
	}
	if _, ok := otlpvalue.Int(attributes, "as.string"); ok {
		t.Error("intAttribute accepted a string")
	}
	// Some SDKs emit a status as a double; that is still a usable status.
	if got, ok := otlpvalue.Int(attributes, "as.double"); !ok || got != 503 {
		t.Errorf("otlpvalue.Int(as.double) = %d, %v; want 503, true", got, ok)
	}
	if _, ok := otlpvalue.String(attributes, "absent"); ok {
		t.Error("stringAttribute reported a value for a key that is not there")
	}
}
