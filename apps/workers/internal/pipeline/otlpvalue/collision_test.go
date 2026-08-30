package otlpvalue

import (
	"encoding/json"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
)

func stringAttr(key, value string) *commonpb.KeyValue {
	return &commonpb.KeyValue{Key: key, Value: &commonpb.AnyValue{
		Value: &commonpb.AnyValue_StringValue{StringValue: value},
	}}
}

func mapAttr(key string, inner ...*commonpb.KeyValue) *commonpb.KeyValue {
	return &commonpb.KeyValue{Key: key, Value: &commonpb.AnyValue{
		Value: &commonpb.AnyValue_KvlistValue{
			KvlistValue: &commonpb.KeyValueList{Values: inner},
		},
	}}
}

// The record that stopped ingest for every tenant: the demo's PHP service logs
// `context` as an object holding `total`, and `context.total` beside it. Both
// name one ClickHouse path, and the insert is refused outright.
func TestTheTwoKeysThatNamedOnePathNoLongerCollide(t *testing.T) {
	values := AttributesMap([]*commonpb.KeyValue{
		mapAttr("context", stringAttr("total", "26.97")),
		stringAttr("context.total", "26.97"),
	})

	if got := AttributeAt(values, "context.total"); got != "26.97" {
		t.Errorf("context.total = %v, want the first writer's value", got)
	}
	dropped, ok := AttributeAt(values, DroppedAttributesKey).([]string)
	if !ok || len(dropped) != 1 || dropped[0] != "context.total" {
		t.Errorf("dropped = %v, want the key that lost recorded", AttributeAt(values, DroppedAttributesKey))
	}
	if _, err := json.Marshal(values); err != nil {
		t.Fatalf("the resolved document does not marshal: %v", err)
	}
}

// The reverse order has to resolve too: a leaf already sitting where an object
// needs to descend cannot be silently destroyed to make room.
func TestALeafDoesNotGetOverwrittenByAnObjectNeedingItsPath(t *testing.T) {
	values := AttributesMap([]*commonpb.KeyValue{
		stringAttr("context.total", "flat first"),
		mapAttr("context", stringAttr("total", "object second")),
	})

	if got := AttributeAt(values, "context.total"); got != "flat first" {
		t.Errorf("context.total = %v, want the first writer's value", got)
	}
	dropped, _ := AttributeAt(values, DroppedAttributesKey).([]string)
	if len(dropped) != 1 || dropped[0] != "context" {
		t.Errorf("dropped = %v, want context recorded", dropped)
	}
}

// Nesting is what ClickHouse does anyway — measured, a flat dotted key and the
// nested form store identically — so ordinary attributes must be unchanged.
func TestAnOrdinaryDottedKeyIsStoredAtItsPath(t *testing.T) {
	values := AttributesMap([]*commonpb.KeyValue{
		stringAttr("http.request.method", "GET"),
		stringAttr("service.name", "checkout"),
	})

	if got := AttributeAt(values, "http.request.method"); got != "GET" {
		t.Errorf("http.request.method = %v", got)
	}
	if got := AttributeAt(values, "service.name"); got != "checkout" {
		t.Errorf("service.name = %v", got)
	}
	if AttributeAt(values, DroppedAttributesKey) != nil {
		t.Errorf("nothing collided, so nothing should be recorded as dropped")
	}
}

func TestAKeylessAttributeIsSkippedRatherThanCreatingAnEmptyPath(t *testing.T) {
	values := AttributesMap([]*commonpb.KeyValue{stringAttr("", "orphan")})

	if len(values) != 0 {
		t.Errorf("values = %v, want nothing", values)
	}
}
