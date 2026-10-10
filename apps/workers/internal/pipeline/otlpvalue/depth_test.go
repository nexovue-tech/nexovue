package otlpvalue

import (
	"encoding/json"
	"strings"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
)

// clickhouseRefusesAt is a depth the development ClickHouse 26.3 refused with
// INCORRECT_DATA (100 levels were accepted, 1,100 refused). Nothing stored may
// come near it.
const clickhouseRefusesAt = 1000

func stringAttribute(key string) *commonpb.KeyValue {
	return &commonpb.KeyValue{Key: key, Value: &commonpb.AnyValue{
		Value: &commonpb.AnyValue_StringValue{StringValue: "v"}}}
}

// jsonDepth is how deeply a stored value nests, counted as ClickHouse counts it:
// one level per object or array.
func jsonDepth(t *testing.T, value any) int {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	deepest, current := 0, 0
	for _, c := range string(encoded) {
		switch c {
		case '{', '[':
			current++
			deepest = max(deepest, current)
		case '}', ']':
			current--
		}
	}
	return deepest
}

func TestAKeyDeeperThanTheCapIsDroppedAndNamed(t *testing.T) {
	// The attribute that stopped the span pipeline: 1,100 levels from its key
	// alone, which ClickHouse refuses outright.
	deep := strings.Repeat("a.", 1099) + "a"

	values := AttributesMap([]*commonpb.KeyValue{stringAttribute(deep), stringAttribute("http.method")})

	if got := jsonDepth(t, values); got >= clickhouseRefusesAt {
		t.Fatalf("stored value nests %d levels; ClickHouse refuses this", got)
	}
	if AttributeAt(values, "http.method") != "v" {
		t.Error("a well-formed attribute beside the deep one was lost")
	}
	dropped, _ := AttributeAt(values, DroppedAttributesKey).([]string)
	if len(dropped) != 1 || dropped[0] != deep {
		t.Errorf("dropped = %v, want the deep key named so the loss is visible", dropped)
	}
}

func TestAKeyAtTheCapIsKept(t *testing.T) {
	atCap := strings.Repeat("a.", MaxDepth-1) + "a"
	values := AttributesMap([]*commonpb.KeyValue{stringAttribute(atCap)})
	if AttributeAt(values, atCap) != "v" {
		t.Errorf("a %d-level key was dropped; the cap must not cost a key that fits", MaxDepth)
	}
}

func TestAValueNestedPastTheCapBecomesNullWhereItCrosses(t *testing.T) {
	// A key-value list inside a key-value list, 1,100 deep: the other way to nest.
	value := &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: "leaf"}}
	for range 1100 {
		value = &commonpb.AnyValue{Value: &commonpb.AnyValue_KvlistValue{KvlistValue: &commonpb.KeyValueList{
			Values: []*commonpb.KeyValue{{Key: "k", Value: value}}}}}
	}

	values := AttributesMap([]*commonpb.KeyValue{{Key: "nested", Value: value}})

	if got := jsonDepth(t, values); got >= clickhouseRefusesAt {
		t.Fatalf("stored value nests %d levels; ClickHouse refuses this", got)
	}
	if _, kept := values["nested"]; !kept {
		t.Error("the attribute vanished; its key must stay so the gap is visible")
	}
}
