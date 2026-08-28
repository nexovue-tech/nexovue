package logwriting

import (
	"encoding/base64"
	"encoding/json"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
)

// emptyJSONObject is what a row with no attributes carries. The JSON columns
// are not nullable, so every row must hold a parsable document.
const emptyJSONObject = "{}"

// attributesJSON renders OTLP attributes as a JSON object for ClickHouse's
// native JSON columns, where each attribute path becomes its own subcolumn.
func attributesJSON(attributes []*commonpb.KeyValue) string {
	if len(attributes) == 0 {
		return emptyJSONObject
	}
	encoded, err := json.Marshal(attributesMap(attributes))
	if err != nil {
		// Every value goes through anyValue, which only ever yields types
		// encoding/json can render, so this is unreachable rather than handled.
		return emptyJSONObject
	}
	return string(encoded)
}

func attributesMap(attributes []*commonpb.KeyValue) map[string]any {
	values := make(map[string]any, len(attributes))
	for _, attribute := range attributes {
		if attribute == nil || attribute.GetKey() == "" {
			continue
		}
		values[attribute.GetKey()] = anyValue(attribute.GetValue())
	}
	return values
}

// anyValue converts one OTLP value to its JSON equivalent. An unset or
// unrecognised value becomes null rather than being dropped, so the attribute
// key still appears and the gap is visible in a query.
func anyValue(value *commonpb.AnyValue) any {
	if value == nil {
		return nil
	}
	switch typed := value.GetValue().(type) {
	case *commonpb.AnyValue_StringValue:
		return typed.StringValue
	case *commonpb.AnyValue_BoolValue:
		return typed.BoolValue
	case *commonpb.AnyValue_IntValue:
		return typed.IntValue
	case *commonpb.AnyValue_DoubleValue:
		return typed.DoubleValue
	case *commonpb.AnyValue_BytesValue:
		return base64.StdEncoding.EncodeToString(typed.BytesValue)
	case *commonpb.AnyValue_ArrayValue:
		return arrayValue(typed.ArrayValue)
	case *commonpb.AnyValue_KvlistValue:
		return attributesMap(typed.KvlistValue.GetValues())
	default:
		return nil
	}
}

func arrayValue(array *commonpb.ArrayValue) []any {
	source := array.GetValues()
	values := make([]any, 0, len(source))
	for _, element := range source {
		values = append(values, anyValue(element))
	}
	return values
}

// stringAttribute returns the value of key when it is a string, which is the
// only shape a promoted string column can take.
func stringAttribute(attributes []*commonpb.KeyValue, key string) string {
	for _, attribute := range attributes {
		if attribute.GetKey() != key {
			continue
		}
		if text, isString := attribute.GetValue().GetValue().(*commonpb.AnyValue_StringValue); isString {
			return text.StringValue
		}
		return ""
	}
	return ""
}

// jsonText renders any Go value the attribute conversion produces. It is what
// `body` holds for a non-string body (ADR 0062).
func jsonText(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return string(encoded)
}
