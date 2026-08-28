// Package otlpvalue renders OTLP attribute values into the shapes ClickHouse
// columns take.
//
// Every signal needs this and each pipeline had grown its own copy — three
// near-identical files that had already drifted to 99, 112 and 113 lines within
// a day of each other. What drifts is not the obvious part: it is whether a
// bytes value is base64 or dropped, whether an unset value becomes null or
// vanishes, whether an id is padded or truncated. Three answers to those
// questions is three shapes of row for one shape of input.
package otlpvalue

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
)

// EmptyObject is what a row with no attributes carries. The JSON columns are
// not nullable, so every row must hold a parsable document.
const EmptyObject = "{}"

// Widths of the hex-encoded id columns, which are FixedString in ClickHouse and
// so must be written at exactly their declared size.
const (
	TraceIDHexLen = 32
	SpanIDHexLen  = 16
)

// AttributesJSON renders OTLP attributes as a JSON object for ClickHouse's
// native JSON columns, where each attribute path becomes its own subcolumn.
func AttributesJSON(attributes []*commonpb.KeyValue) string {
	if len(attributes) == 0 {
		return EmptyObject
	}
	return JSONText(AttributesMap(attributes))
}

// JSONText renders any value this package produced. Marshalling cannot fail on
// them — Value only ever yields types encoding/json renders — so a failure
// returns an empty object rather than propagating an error nothing can act on.
func JSONText(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return EmptyObject
	}
	return string(encoded)
}

// AttributesMap converts OTLP attributes to a plain map. An attribute with no
// key is skipped: it cannot be addressed as a subcolumn.
func AttributesMap(attributes []*commonpb.KeyValue) map[string]any {
	values := make(map[string]any, len(attributes))
	for _, attribute := range attributes {
		if attribute == nil || attribute.GetKey() == "" {
			continue
		}
		values[attribute.GetKey()] = Value(attribute.GetValue())
	}
	return values
}

// Value converts one OTLP value to its JSON equivalent. An unset or
// unrecognised value becomes null rather than being dropped, so the attribute
// key still appears and the gap is visible in a query.
func Value(value *commonpb.AnyValue) any {
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
		return ArrayValue(typed.ArrayValue)
	case *commonpb.AnyValue_KvlistValue:
		return AttributesMap(typed.KvlistValue.GetValues())
	default:
		return nil
	}
}

// ArrayValue converts an OTLP array, preserving element order and position — an
// unrenderable element becomes null rather than shortening the array.
func ArrayValue(array *commonpb.ArrayValue) []any {
	source := array.GetValues()
	values := make([]any, 0, len(source))
	for _, element := range source {
		values = append(values, Value(element))
	}
	return values
}

// Find returns the value stored under key.
func Find(attributes []*commonpb.KeyValue, key string) (*commonpb.AnyValue, bool) {
	for _, attribute := range attributes {
		if attribute.GetKey() == key {
			return attribute.GetValue(), true
		}
	}
	return nil, false
}

// String returns the value of key when it is a string, which is the only shape
// a promoted string column can take. The boolean distinguishes "absent" from
// "present and empty", which callers promoting a column need to tell apart.
func String(attributes []*commonpb.KeyValue, key string) (string, bool) {
	value, ok := Find(attributes, key)
	if !ok {
		return "", false
	}
	text, isString := value.GetValue().(*commonpb.AnyValue_StringValue)
	if !isString {
		return "", false
	}
	return text.StringValue, true
}

// StringOrEmpty is String for callers that treat absent and empty alike.
func StringOrEmpty(attributes []*commonpb.KeyValue, key string) string {
	text, _ := String(attributes, key)
	return text
}

// Int returns the value of key as an integer.
//
// A double is accepted and truncated, because a JSON-encoded exporter has no
// integer type: an HTTP status arrives as 503.0 from those SDKs and as 503 from
// the rest, and a column that took only one of them would be empty for half the
// world. Refusing the double was tried and a test caught it within the minute.
func Int(attributes []*commonpb.KeyValue, key string) (int64, bool) {
	value, ok := Find(attributes, key)
	if !ok {
		return 0, false
	}
	switch typed := value.GetValue().(type) {
	case *commonpb.AnyValue_IntValue:
		return typed.IntValue, true
	case *commonpb.AnyValue_DoubleValue:
		return int64(typed.DoubleValue), true
	default:
		return 0, false
	}
}

// HexID renders a trace or span id as the lower-case hex of exactly width
// bytes, which is what the FixedString columns store.
//
// Width is enforced rather than assumed: ClickHouse pads a short FixedString
// with NULs and rejects a long one, so an id of the wrong length would either
// corrupt a lookup silently or fail the whole insert. A missing id becomes
// zeroes, which reads as "absent" everywhere it is compared.
func HexID(id []byte, width int) []byte {
	padded := make([]byte, width)
	encoded := make([]byte, hex.EncodedLen(len(id)))
	hex.Encode(encoded, id)
	// copy, not hex.Encode into padded: an id longer than the column would
	// overrun the destination, and copy truncates where Encode would panic.
	copy(padded, encoded)
	return padded
}
