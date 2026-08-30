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
	"strings"

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

// DroppedAttributesKey names the attributes a record lost to a path collision.
//
// The loss is written into the record rather than only logged, so the person
// reading the line can see that something was there. A silent drop would be
// indistinguishable from the sender never having set it.
const DroppedAttributesKey = "nexovue.dropped_attributes"

// AttributesMap converts OTLP attributes to the nested shape ClickHouse stores.
//
// A dot in an attribute key is a literal character to OTLP and a path separator
// to ClickHouse, so `http.request.method` and a nested object reach the same
// subcolumn — measured: both store as {"http":{"request":{"method":...}}} and
// both answer `.http.request.method`. Building that shape here rather than
// leaving it to the database changes nothing about what is stored, and is what
// makes a collision visible to us first.
//
// It has to be visible, because ClickHouse cannot represent one. A sender that
// sets both `context` (an object holding `total`) and `context.total` names one
// path twice, and the insert is refused outright — INCORRECT_DATA, which is
// permanent. That took every pipeline down for an hour: the demo's PHP service
// logs exactly that shape. Neither escape hatch works — backticks are stored as
// literal characters, and `type_json_skip_duplicated_paths` drops a value with
// no trace. So the collision is resolved here, first writer winning, and the
// keys that lost are recorded under DroppedAttributesKey.
//
// An attribute with no key is skipped: it cannot be addressed as a subcolumn.
func AttributesMap(attributes []*commonpb.KeyValue) map[string]any {
	values := make(map[string]any, len(attributes))
	var dropped []string

	for _, attribute := range attributes {
		if attribute == nil || attribute.GetKey() == "" {
			continue
		}
		if !insertAtPath(values, splitPath(attribute.GetKey()), Value(attribute.GetValue())) {
			dropped = append(dropped, attribute.GetKey())
		}
	}

	if dropped != nil {
		// Recorded through the same path logic, so it cannot itself collide
		// with a real attribute and take the record down.
		insertAtPath(values, splitPath(DroppedAttributesKey), dropped)
	}
	return values
}

// AttributeAt reads an attribute back out of the nested shape by its OTLP key,
// which is how a caller asks for `service.name` without knowing it is stored two
// levels down. Returns nil when the key is not there.
func AttributeAt(values map[string]any, key string) any {
	path := splitPath(key)
	for _, step := range path[:len(path)-1] {
		nested, ok := values[step].(map[string]any)
		if !ok {
			return nil
		}
		values = nested
	}
	return values[path[len(path)-1]]
}

// splitPath turns an attribute key into the subcolumn path ClickHouse gives it.
func splitPath(key string) []string {
	return strings.Split(key, ".")
}

// insertAtPath places a value at path, reporting whether it fitted.
//
// It fails rather than overwrites, because the first writer holds the path and
// a later arrival must not silently replace it. Two ways to not fit: the path
// is already taken, or a step along the way is a leaf that would have to be
// destroyed to descend through it.
func insertAtPath(values map[string]any, path []string, value any) bool {
	parent := values
	for _, step := range path[:len(path)-1] {
		switch existing := parent[step].(type) {
		case nil:
			created := map[string]any{}
			parent[step] = created
			parent = created
		case map[string]any:
			parent = existing
		default:
			return false // a leaf sits where this key needs an object
		}
	}

	leaf := path[len(path)-1]
	if _, taken := parent[leaf]; taken {
		return false
	}
	parent[leaf] = value
	return true
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
