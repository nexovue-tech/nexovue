package mask_test

import (
	"strings"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
)

// Card numbers from the test set every payment processor publishes, so no real
// number is ever in this repository. Both pass Luhn, which is the point.
const (
	testCardNumber      = "4111111111111111"
	otherTestCardNumber = "5555555555554444"
)

// A 32-byte secret, the shortest NewHashKey accepts. Test-only.
const (
	testHashSecret  = "0123456789abcdef0123456789abcdef"
	otherHashSecret = "fedcba9876543210fedcba9876543210"
)

func hashKey(t *testing.T, secret string) mask.HashKey {
	t.Helper()
	key, err := mask.NewHashKey([]byte(secret))
	if err != nil {
		t.Fatalf("NewHashKey() = %v, want no error", err)
	}
	return key
}

func rulesFor(t *testing.T, config string, options ...mask.Option) mask.Rules {
	t.Helper()
	rules, err := mask.RulesFromJSON([]byte(config), options...)
	if err != nil {
		t.Fatalf("RulesFromJSON(%s) = %v, want no error", config, err)
	}
	return rules
}

func stringAttribute(key, value string) *commonpb.KeyValue {
	return &commonpb.KeyValue{
		Key:   key,
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}},
	}
}

// maskWith runs one ruleset over a single attribute and returns what is left of
// its value.
func maskWith(t *testing.T, rules mask.Rules, key, value string) string {
	t.Helper()
	attribute := stringAttribute(key, value)
	span := &tracepb.Span{Attributes: []*commonpb.KeyValue{attribute}}
	batch := &tracepb.ResourceSpans{
		ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span}}},
	}

	masker := mask.New(mask.StaticRules(rules))
	masker.MaskResourceSpans("any-project", []*tracepb.ResourceSpans{batch})
	return attribute.GetValue().GetStringValue()
}

// maskOne runs the default rules over a single attribute.
func maskOne(t *testing.T, key, value string) string {
	t.Helper()
	return maskWith(t, mask.Defaults(), key, value)
}

// Every rule that is on out of the box, and the shapes it has to catch.
func TestDefaultRulesMaskWhatCanNeverBeNeededForDebugging(t *testing.T) {
	cases := map[string]struct{ key, value string }{
		"authorization header":   {"http.request.header.authorization", "Bearer abc.def"},
		"proxy authorization":    {"http.request.header.proxy-authorization", "Basic Zm9v"},
		"request cookie":         {"http.request.header.cookie", "session=abc"},
		"response set-cookie":    {"http.response.header.set-cookie", "session=abc; Path=/"},
		"underscored header":     {"http.request.header_authorization", "Bearer abc"},
		"password key":           {"db.password", "hunter2"},
		"uppercase password key": {"DB.PASSWORD", "hunter2"},
		"secret key":             {"app.client_secret", "s3cr3t"},
		"token key":              {"auth.access_token", "abc.def.ghi"},
		"api key underscored":    {"vendor.api_key", "nvk_live_x"},
		"api key hyphenated":     {"vendor.api-key", "nvk_live_x"},
		"api key run together":   {"vendor.apikey", "nvk_live_x"},
		"card number alone":      {"payment.reference", testCardNumber},
		"card number in a sentence": {"error.detail",
			"charge failed for card " + testCardNumber + " at checkout"},
		"card number with spaces": {"payment.reference", "4111 1111 1111 1111"},
		"card number with dashes": {"payment.reference", "4111-1111-1111-1111"},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			masked := maskOne(t, tc.key, tc.value)
			if strings.Contains(masked, tc.value) || strings.Contains(masked, testCardNumber) {
				t.Errorf("value for %q is %q, want the sensitive part replaced", tc.key, masked)
			}
			if !strings.Contains(masked, mask.Placeholder) {
				t.Errorf("value for %q is %q, want it to contain %s",
					tc.key, masked, mask.Placeholder)
			}
		})
	}
}

// Masking is irreversible, so a default rule that fires on ordinary telemetry
// destroys the data an engineer came to read.
func TestDefaultRulesLeaveOrdinaryTelemetryAlone(t *testing.T) {
	cases := map[string]struct{ key, value string }{
		"http route":                {"http.route", "/checkout"},
		"db statement":              {"db.statement", "SELECT * FROM carts WHERE id = $1"},
		"user agent":                {"http.request.header.user-agent", "curl/8.4.0"},
		"order id":                  {"order.id", "1234567890123"},
		"digits failing luhn":       {"order.id", "4111111111111112"},
		"twenty digits":             {"trace.correlation", "12345678901234567890"},
		"twelve digits":             {"invoice.number", "411111111111"},
		"timestamp":                 {"event.at", "2026-08-27T10:00:00Z"},
		"cookie policy url":         {"policy.url", "https://example.test/cookies-policy"},
		"tokenizer description":     {"model.name", "sentencepiece"},
		"gen_ai input token count":  {"gen_ai.usage.input_tokens", "842"},
		"gen_ai output token count": {"gen_ai.usage.output_tokens", "156"},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if masked := maskOne(t, tc.key, tc.value); masked != tc.value {
				t.Errorf("value for %q became %q, want it untouched (%q)", tc.key, masked, tc.value)
			}
		})
	}
}

func TestMaskingReachesEverywhereASpanCarriesText(t *testing.T) {
	resourceAttr := stringAttribute("service.api_key", "nvk_live_x")
	spanAttr := stringAttribute("http.request.header.authorization", "Bearer abc")
	eventAttr := stringAttribute("exception.message", "declined "+testCardNumber)
	linkAttr := stringAttribute("linked.password", "hunter2")
	nested := &commonpb.KeyValue{
		Key: "checkout.payload",
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_ArrayValue{
			ArrayValue: &commonpb.ArrayValue{Values: []*commonpb.AnyValue{
				{Value: &commonpb.AnyValue_StringValue{StringValue: testCardNumber}},
			}},
		}},
	}

	span := &tracepb.Span{
		Attributes: []*commonpb.KeyValue{spanAttr, nested},
		Events:     []*tracepb.Span_Event{{Attributes: []*commonpb.KeyValue{eventAttr}}},
		Links:      []*tracepb.Span_Link{{Attributes: []*commonpb.KeyValue{linkAttr}}},
		Status:     &tracepb.Status{Message: "card " + testCardNumber + " declined"},
	}
	batch := &tracepb.ResourceSpans{
		Resource:   &resourcepb.Resource{Attributes: []*commonpb.KeyValue{resourceAttr}},
		ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span}}},
	}

	masker := mask.New(mask.StaticRules(mask.Defaults()))
	masked := masker.MaskResourceSpans("any-project", []*tracepb.ResourceSpans{batch})

	if masked != 6 {
		t.Errorf("MaskResourceSpans() reported %d masked values, want 6", masked)
	}
	for _, place := range []struct {
		name  string
		value string
	}{
		{"resource attribute", resourceAttr.GetValue().GetStringValue()},
		{"span attribute", spanAttr.GetValue().GetStringValue()},
		{"event attribute", eventAttr.GetValue().GetStringValue()},
		{"link attribute", linkAttr.GetValue().GetStringValue()},
		{"nested array element", nested.GetValue().GetArrayValue().GetValues()[0].GetStringValue()},
		{"status message", span.GetStatus().GetMessage()},
	} {
		if strings.Contains(place.value, testCardNumber) || strings.Contains(place.value, "Bearer") {
			t.Errorf("%s is %q, want it masked", place.name, place.value)
		}
	}
}

func TestProjectRulesLayerOverTheDefaults(t *testing.T) {
	cases := map[string]struct {
		config    string
		key       string
		value     string
		wantMask  bool
		wantError bool
	}{
		"extra exact key": {
			config: `{"mask_keys":["user.email"]}`, key: "user.email",
			value: "a@example.test", wantMask: true,
		},
		"extra substring": {
			config: `{"mask_key_contains":["ssn"]}`, key: "customer.ssn_last4",
			value: "1234", wantMask: true,
		},
		"disabling a family": {
			config: `{"disable":["cookies"]}`, key: "http.request.header.cookie",
			value: "session=abc", wantMask: false,
		},
		"disabling cookies keeps credentials on": {
			config: `{"disable":["cookies"]}`, key: "db.password",
			value: "hunter2", wantMask: true,
		},
		"unknown family is refused": {
			config: `{"disable":["everything"]}`, wantError: true,
		},
		"malformed document is refused": {
			config: `{"disable":`, wantError: true,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			rules, err := mask.RulesFromJSON([]byte(tc.config))
			if tc.wantError {
				if err == nil {
					t.Fatalf("RulesFromJSON(%s) succeeded, want an error", tc.config)
				}
				return
			}
			if err != nil {
				t.Fatalf("RulesFromJSON(%s) = %v, want no error", tc.config, err)
			}

			got := maskWith(t, rules, tc.key, tc.value)
			if masked := got == mask.Placeholder; masked != tc.wantMask {
				t.Errorf("value for %q is %q; masked = %v, want %v",
					tc.key, got, masked, tc.wantMask)
			}
		})
	}
}

// A matching rule is not the last word. Masking is irreversible, so an operator
// who hits a false positive needs a remedy narrower than switching the rule off
// — which is how a project ends up with no masking at all.
func TestAllowlistedValuesSurviveABroaderBlock(t *testing.T) {
	cases := map[string]struct {
		config     string
		key, value string
		wantMask   bool
	}{
		"allowlisted id that passes luhn": {
			config: `{"allow_values":["` + testCardNumber + `"]}`,
			key:    "order.reference", value: testCardNumber,
		},
		"allowlisted card run inside a sentence": {
			config: `{"allow_values":["` + testCardNumber + `"]}`,
			key:    "error.detail", value: "charge failed for card " + testCardNumber,
		},
		"allowlisted value under a blocked key": {
			config: `{"allow_values":["public-demo-token"]}`,
			key:    "vendor.api_key", value: "public-demo-token",
		},
		"allowlist matches case-insensitively": {
			config: `{"allow_values":["Public-Demo-Token"]}`,
			key:    "vendor.api_key", value: "public-demo-token",
		},
		"allowlist beats a project's own key rule": {
			config: `{"mask_keys":["user.email"],"allow_values":["support@example.test"]}`,
			key:    "user.email", value: "support@example.test",
		},
		"another value under the same key is still masked": {
			config: `{"allow_values":["public-demo-token"]}`,
			key:    "vendor.api_key", value: "nvk_live_x", wantMask: true,
		},
		"another card is still masked": {
			config: `{"allow_values":["` + testCardNumber + `"]}`,
			key:    "payment.reference", value: otherTestCardNumber, wantMask: true,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got := maskWith(t, rulesFor(t, tc.config), tc.key, tc.value)
			if tc.wantMask {
				if got == tc.value {
					t.Errorf("value for %q is untouched (%q), want it masked", tc.key, got)
				}
				return
			}
			if got != tc.value {
				t.Errorf("value for %q became %q, want it allowed through (%q)",
					tc.key, got, tc.value)
			}
		})
	}
}

// Hashing exists for correlation: the answer to "the same user across these
// requests" has to survive masking, while who that user is does not.
func TestHashingKeepsEqualValuesEqualWithoutRevealingThem(t *testing.T) {
	const config = `{"hash_keys":["user.email"]}`
	rules := rulesFor(t, config, mask.WithHashKey(hashKey(t, testHashSecret)))

	first := maskWith(t, rules, "user.email", "ada@example.test")
	again := maskWith(t, rules, "user.email", "ada@example.test")
	other := maskWith(t, rules, "user.email", "grace@example.test")

	if first != again {
		t.Errorf("the same value hashed to %q and %q, want one stable pseudonym", first, again)
	}
	if first == other {
		t.Errorf("two different values both hashed to %q, want them to differ", first)
	}
	if strings.Contains(first, "ada") || strings.Contains(first, "example.test") {
		t.Errorf("pseudonym is %q, want nothing of the original in it", first)
	}
	if first == mask.Placeholder || !strings.HasPrefix(first, "[masked") {
		t.Errorf("pseudonym is %q, want a value that still reads as masked", first)
	}

	// A different secret has to produce a different pseudonym, or the key is
	// not what protects the value.
	elsewhere := rulesFor(t, config, mask.WithHashKey(hashKey(t, otherHashSecret)))
	if got := maskWith(t, elsewhere, "user.email", "ada@example.test"); got == first {
		t.Errorf("a different hash key produced the same pseudonym %q", got)
	}
}

// One project hashes what it investigates with and blanks the rest, so the
// action belongs to the rule rather than to the worker.
func TestARuleSetMixesHashingAndBlanking(t *testing.T) {
	const config = `{
		"mask_keys": ["user.email"],
		"mask_key_contains": ["ssn"],
		"hash_keys": ["user.id"],
		"hash_key_contains": ["account_id"],
		"hash": ["card_numbers"]
	}`
	rules := rulesFor(t, config, mask.WithHashKey(hashKey(t, testHashSecret)))

	cases := map[string]struct {
		key, value string
		wantHash   bool
	}{
		"blanked exact key":       {"user.email", "ada@example.test", false},
		"blanked key substring":   {"customer.ssn_last4", "1234", false},
		"blanked default family":  {"db.password", "hunter2", false},
		"hashed exact key":        {"user.id", "u-42", true},
		"hashed key substring":    {"billing.account_id", "acct-7", true},
		"hashed default family":   {"payment.reference", testCardNumber, true},
		"hashed card in sentence": {"error.detail", "declined " + testCardNumber, true},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got := maskWith(t, rules, tc.key, tc.value)
			if strings.Contains(got, tc.value) || strings.Contains(got, testCardNumber) {
				t.Fatalf("value for %q is %q, want the sensitive part gone", tc.key, got)
			}
			if hashed := !strings.Contains(got, mask.Placeholder); hashed != tc.wantHash {
				t.Errorf("value for %q is %q; hashed = %v, want %v",
					tc.key, got, hashed, tc.wantHash)
			}
		})
	}
}

// A pseudonym nobody can reproduce after a restart is worthless, so the key is
// deployment state and a document that needs one without it is a fault worth
// naming rather than quietly blanking.
func TestHashingIsRefusedWithoutAKey(t *testing.T) {
	for name, config := range map[string]string{
		"hashed family":        `{"hash":["cookies"]}`,
		"hashed exact key":     `{"hash_keys":["user.id"]}`,
		"hashed key substring": `{"hash_key_contains":["account_id"]}`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := mask.RulesFromJSON([]byte(config)); err == nil {
				t.Errorf("RulesFromJSON(%s) succeeded without a hash key, want an error", config)
			}
		})
	}

	// A family that is off cannot hash anything, so it needs no key.
	const disabled = `{"disable":["cookies"],"hash":["cookies"]}`
	if _, err := mask.RulesFromJSON([]byte(disabled)); err != nil {
		t.Errorf("RulesFromJSON(%s) = %v, want no error", disabled, err)
	}
}

func TestHashKeyRefusesAShortSecretAndNeverPrintsItself(t *testing.T) {
	if _, err := mask.NewHashKey([]byte("too short to be a key")); err == nil {
		t.Error("NewHashKey() accepted a short secret, want an error")
	}
	if printed := hashKey(t, testHashSecret).String(); strings.Contains(printed, testHashSecret) {
		t.Errorf("HashKey prints as %q, want the secret kept out of every log line", printed)
	}
}
