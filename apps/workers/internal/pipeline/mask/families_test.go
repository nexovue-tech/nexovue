package mask

import (
	"reflect"
	"testing"
)

// TestEveryRuleFamilyIsReachableFromFamilies is a structural test, not a
// behavioural one: it counts the `family` fields on Rules by reflection and
// asserts families() returns exactly that many.
//
// It exists because a family was added to the struct, to Defaults, to
// setFamily, to DefaultRuleNames and to masksValues — and missed here. The
// omission is invisible: families() has one caller, hashesAnything, and a
// family it cannot see simply never trips the guard that refuses hashing on a
// worker with no hash key. Nothing fails, and credentials get hashed with an
// empty key.
//
// A list a human maintains alongside a struct will drift. This makes the drift
// fail a test instead.
func TestEveryRuleFamilyIsReachableFromFamilies(t *testing.T) {
	rules := Defaults()

	declared := 0
	value := reflect.ValueOf(rules)
	for i := range value.NumField() {
		if value.Field(i).Type() == reflect.TypeOf(family{}) {
			declared++
		}
	}

	if declared == 0 {
		t.Fatal("no family fields found on Rules; this test is not testing what it thinks")
	}
	if got := len(rules.families()); got != declared {
		t.Errorf("families() returns %d of %d family fields on Rules — one was added to the "+
			"struct and not to families(), so hashesAnything cannot see it", got, declared)
	}
}

// TestHashingAnyDefaultFamilyIsRefusedWithoutAHashKey walks every default rule
// and asks for it to be hashed on a worker with no hash key configured.
//
// Every one must be refused. This is the guard families() feeds, checked
// through its real entry point rather than through the helper.
func TestHashingAnyDefaultFamilyIsRefusedWithoutAHashKey(t *testing.T) {
	for _, name := range DefaultRuleNames {
		t.Run(name, func(t *testing.T) {
			document := []byte(`{"hash":["` + name + `"]}`)
			if _, err := RulesFromJSON(document, WithHashKey(HashKey{})); err == nil {
				t.Errorf("hashing %q was accepted with no hash key: a matched value would be "+
					"digested with an empty HMAC key, which is what the key exists to prevent", name)
			}
		})
	}
}
