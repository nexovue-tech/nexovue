package mask_test

import (
	"strings"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
)

// A card number from the test set every payment processor publishes, so no real
// number is ever in this repository. It passes Luhn, which is the point.
const testCardNumber = "4111111111111111"

func stringAttribute(key, value string) *commonpb.KeyValue {
	return &commonpb.KeyValue{
		Key:   key,
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}},
	}
}

// maskOne runs the default rules over a single attribute and returns what is
// left of its value.
func maskOne(t *testing.T, key, value string) string {
	t.Helper()
	attribute := stringAttribute(key, value)
	span := &tracepb.Span{Attributes: []*commonpb.KeyValue{attribute}}
	batch := &tracepb.ResourceSpans{
		ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span}}},
	}

	masker := mask.New(mask.StaticRules(mask.Defaults()))
	masker.MaskResourceSpans("any-project", []*tracepb.ResourceSpans{batch})
	return attribute.GetValue().GetStringValue()
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
		"http route":            {"http.route", "/checkout"},
		"db statement":          {"db.statement", "SELECT * FROM carts WHERE id = $1"},
		"user agent":            {"http.request.header.user-agent", "curl/8.4.0"},
		"order id":              {"order.id", "1234567890123"},
		"digits failing luhn":   {"order.id", "4111111111111112"},
		"twenty digits":         {"trace.correlation", "12345678901234567890"},
		"twelve digits":         {"invoice.number", "411111111111"},
		"timestamp":             {"event.at", "2026-08-27T10:00:00Z"},
		"cookie policy url":     {"policy.url", "https://example.test/cookies-policy"},
		"tokenizer description": {"model.name", "sentencepiece"},
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

			attribute := stringAttribute(tc.key, tc.value)
			span := &tracepb.Span{Attributes: []*commonpb.KeyValue{attribute}}
			batch := &tracepb.ResourceSpans{
				ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span}}},
			}
			mask.New(mask.StaticRules(rules)).
				MaskResourceSpans("any-project", []*tracepb.ResourceSpans{batch})

			got := attribute.GetValue().GetStringValue()
			if masked := got == mask.Placeholder; masked != tc.wantMask {
				t.Errorf("value for %q is %q; masked = %v, want %v",
					tc.key, got, masked, tc.wantMask)
			}
		})
	}
}
