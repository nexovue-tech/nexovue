package allowance

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

// The allowance rule, asserted from Go against the shared cases.
//
// `packages/contracts/tests/test_allowance_rule.py` reads the same file and
// states the same expectations independently. Two readers agreeing is what makes
// "one rule in two languages" a fact rather than a comment.
//
// **Why this file exists.** Both implementations carry a comment saying the
// other must agree with them — "this is the rule, and it is written once here
// and once in `nexovue_api.usage.billing`. The two have to agree or a customer
// is refused at a limit the screen never showed them" — and nothing checked. A
// divergence is silent on both sides: the screen shows one allowance and this
// door applies another, and the customer's telemetry stops at a number they were
// never told.
//
// AC25.15, `docs/features/usage-billing/spec.md`.

const casesFile = "../../../../packages/contracts/fixtures/allowance-service-count.json"

type allowanceCase struct {
	Name              string `json:"name"`
	MaxBilledServices *int32 `json:"max_billed_services"`
	ServicesCommitted *int32 `json:"services_committed"`
	Expected          *int32 `json:"expected"`
}

func TestAllowanceServiceCountMatchesTheSharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Clean(casesFile))
	if err != nil {
		t.Fatalf("reading the shared cases: %v", err)
	}
	var loaded struct {
		Cases []allowanceCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &loaded); err != nil {
		t.Fatalf("decoding the shared cases: %v", err)
	}
	if len(loaded.Cases) == 0 {
		t.Fatal("the shared cases file is empty, so neither language is being checked")
	}

	for _, each := range loaded.Cases {
		t.Run(each.Name, func(t *testing.T) {
			got := allowanceServiceCount(each.MaxBilledServices, each.ServicesCommitted)
			if !sameCount(got, each.Expected) {
				t.Fatalf(
					"Go sized the allowance on %s, the shared cases say %s. The API reads the "+
						"same file — if this is the correct answer, both implementations and the "+
						"file change together.",
					describe(got), describe(each.Expected),
				)
			}
		})
	}
}

// TestUsageFromEnforcesExactlyWhenACountSizesIt is the half the shared cases
// cannot express: whether a count exists decides whether anything is enforced at
// all, and "enforced with no ceiling" would admit every byte while the screen
// promised a stop.
func TestUsageFromEnforcesExactlyWhenACountSizesIt(t *testing.T) {
	perService := int64(10 << 30)
	committed := int32(26)

	sized := usageFrom(&perService, nil, nil, &committed, 0, 0)
	if !sized.Enforced {
		t.Fatal("a paying tier with a commitment is not enforced; it has a ceiling to apply")
	}
	if want := perService * int64(committed); sized.IncludedBytes != want {
		t.Fatalf("included %d bytes, want %d", sized.IncludedBytes, want)
	}

	unsized := usageFrom(&perService, nil, nil, nil, 0, 0)
	if unsized.Enforced {
		t.Fatal("nothing sizes this allowance, so nothing may be refused against it")
	}

	unpriced := usageFrom(nil, nil, nil, &committed, 0, 0)
	if unpriced.Enforced {
		t.Fatal("no rate recorded is not a ceiling of zero")
	}
}

func sameCount(a, b *int32) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}

func describe(value *int32) string {
	if value == nil {
		return "nothing (enforcing no limit)"
	}
	return strconv.Itoa(int(*value))
}
