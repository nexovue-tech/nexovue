package mask_test

import (
	"strings"
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
)

func stringValue(value string) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}}
}

func mapValue(entries ...*commonpb.KeyValue) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_KvlistValue{
		KvlistValue: &commonpb.KeyValueList{Values: entries},
	}}
}

func arrayValue(elements ...*commonpb.AnyValue) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_ArrayValue{
		ArrayValue: &commonpb.ArrayValue{Values: elements},
	}}
}

// logsWithBody wraps one body in the batch shape the receiver hands the worker.
func logsWithBody(body *commonpb.AnyValue) (*logspb.ResourceLogs, *logspb.LogRecord) {
	record := &logspb.LogRecord{Body: body}
	return &logspb.ResourceLogs{
		ScopeLogs: []*logspb.ScopeLogs{{LogRecords: []*logspb.LogRecord{record}}},
	}, record
}

// maskBody runs one ruleset over a single log body and returns what is left of
// it, rendered flat so a test can assert on the whole tree at once.
func maskBody(t *testing.T, rules mask.Rules, body *commonpb.AnyValue) (string, int) {
	t.Helper()
	batch, record := logsWithBody(body)
	masker := mask.New(mask.StaticRules(rules))
	count := masker.MaskResourceLogs("any-project", []*logspb.ResourceLogs{batch})
	return renderValue(record.GetBody()), count
}

// renderValue flattens an AnyValue to the text it carries, so an assertion can
// say "this secret is nowhere in the body" whatever shape the body has.
func renderValue(value *commonpb.AnyValue) string {
	switch typed := value.GetValue().(type) {
	case *commonpb.AnyValue_StringValue:
		return typed.StringValue
	case *commonpb.AnyValue_ArrayValue:
		var parts []string
		for _, element := range typed.ArrayValue.GetValues() {
			parts = append(parts, renderValue(element))
		}
		return strings.Join(parts, " ")
	case *commonpb.AnyValue_KvlistValue:
		var parts []string
		for _, entry := range typed.KvlistValue.GetValues() {
			parts = append(parts, entry.GetKey()+"="+renderValue(entry.GetValue()))
		}
		return strings.Join(parts, " ")
	default:
		return ""
	}
}

// The body is where logs differ from spans: it is free text or a structure the
// application chose, and it is the likeliest place a secret is printed.
func TestSecretsInALogBodyDoNotSurviveMasking(t *testing.T) {
	cases := map[string]struct {
		body   *commonpb.AnyValue
		secret string
	}{
		"card number in a rendered message": {
			body:   stringValue("charge declined for card " + testCardNumber + " at checkout"),
			secret: testCardNumber,
		},
		"card number in a stack trace": {
			body:   stringValue("panic: charge(" + testCardNumber + ")\n\tat billing.go:42"),
			secret: testCardNumber,
		},
		"credential key in a structured body": {
			body:   mapValue(stringAttribute("password", "hunter2")),
			secret: "hunter2",
		},
		"api key in a structured body": {
			body:   mapValue(stringAttribute("vendor.api_key", "nvk_live_x")),
			secret: "nvk_live_x",
		},
		"authorization header captured into the body": {
			body: mapValue(
				stringAttribute("msg", "request failed"),
				stringAttribute("http.request.header.authorization", "Bearer abc.def"),
			),
			secret: "Bearer abc.def",
		},
		"cookie in a nested map": {
			body: mapValue(&commonpb.KeyValue{
				Key:   "headers",
				Value: mapValue(stringAttribute("set-cookie", "session=abc; Path=/")),
			}),
			secret: "session=abc",
		},
		"card number in an array element": {
			body:   arrayValue(stringValue("retrying"), stringValue(testCardNumber)),
			secret: testCardNumber,
		},
		"secret two levels down under a map inside an array": {
			body:   arrayValue(mapValue(stringAttribute("access_token", "abc.def.ghi"))),
			secret: "abc.def.ghi",
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			rendered, count := maskBody(t, mask.Defaults(), tc.body)
			if strings.Contains(rendered, tc.secret) {
				t.Errorf("body is %q, want %q gone from it", rendered, tc.secret)
			}
			if count == 0 {
				t.Errorf("body is %q but MaskResourceLogs reported nothing masked", rendered)
			}
		})
	}
}

// Masking is irreversible, so a body full of ordinary structured telemetry has
// to come back exactly as it went in. The long digit runs here are the ones that
// broke a chaos run when Luhn was trusted on its own.
func TestOrdinaryLogBodiesAreLeftAlone(t *testing.T) {
	cases := map[string]*commonpb.AnyValue{
		"rendered json with ids that look like cards": stringValue(
			`{"trace":"1787816027743000000","order":"9900112233445566","took_ms":1787833301615}`),
		"service name carrying a nanosecond timestamp": stringValue("chaos-1787816027743000000"),
		"sql statement": stringValue("SELECT * FROM carts WHERE id = $1"),
		"structured body of ordinary fields": mapValue(
			stringAttribute("msg", "checkout completed"),
			stringAttribute("http.route", "/checkout"),
			stringAttribute("order.id", "9900112233445566"),
			stringAttribute("user_agent", "curl/8.4.0"),
		),
		"array of ids": arrayValue(
			stringValue("1234567890123456789"), stringValue("4111111111111112")),
		"digits failing luhn":  stringValue("card 4111111111111112 rejected"),
		"twelve digit invoice": stringValue("invoice 411111111111 settled"),
	}

	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			want := renderValue(body)
			got, count := maskBody(t, mask.Defaults(), body)
			if got != want {
				t.Errorf("body became %q, want it untouched (%q)", got, want)
			}
			if count != 0 {
				t.Errorf("MaskResourceLogs reported %d masked values, want 0", count)
			}
		})
	}
}

// All three places a log record carries a secret, in one batch, with the count
// the pipeline logs.
func TestMaskingReachesEverywhereALogRecordCarriesText(t *testing.T) {
	resourceAttr := stringAttribute("service.api_key", "nvk_live_x")
	recordAttr := stringAttribute("http.request.header.authorization", "Bearer abc")
	bodyEntry := stringAttribute("db.password", "hunter2")
	body := mapValue(stringAttribute("msg", "connect failed for "+testCardNumber), bodyEntry)

	batch := &logspb.ResourceLogs{
		Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{resourceAttr}},
		ScopeLogs: []*logspb.ScopeLogs{{LogRecords: []*logspb.LogRecord{
			{Attributes: []*commonpb.KeyValue{recordAttr}, Body: body},
		}}},
	}

	masker := mask.New(mask.StaticRules(mask.Defaults()))
	masked := masker.MaskResourceLogs("any-project", []*logspb.ResourceLogs{batch})

	if masked != 4 {
		t.Errorf("MaskResourceLogs() reported %d masked values, want 4", masked)
	}
	for _, place := range []struct{ name, value string }{
		{"resource attribute", resourceAttr.GetValue().GetStringValue()},
		{"record attribute", recordAttr.GetValue().GetStringValue()},
		{"body map entry", bodyEntry.GetValue().GetStringValue()},
		{"body rendered message", renderValue(body)},
	} {
		if strings.Contains(place.value, "nvk_live_x") ||
			strings.Contains(place.value, "Bearer") ||
			strings.Contains(place.value, "hunter2") ||
			strings.Contains(place.value, testCardNumber) {
			t.Errorf("%s is %q, want it masked", place.name, place.value)
		}
	}
}

// A nil record, a nil body and a nil batch are all shapes a decoder can hand us,
// and none of them may take the stage down before anything is written.
func TestMaskingLogsToleratesMissingParts(t *testing.T) {
	batches := []*logspb.ResourceLogs{
		nil,
		{},
		{ScopeLogs: []*logspb.ScopeLogs{nil, {LogRecords: []*logspb.LogRecord{nil, {}}}}},
	}
	masker := mask.New(mask.StaticRules(mask.Defaults()))
	if masked := masker.MaskResourceLogs("any-project", batches); masked != 0 {
		t.Errorf("MaskResourceLogs() reported %d masked values, want 0", masked)
	}
}

// Project rules reach into a body exactly as they reach into an attribute:
// masking that only worked on the outermost level would miss the structured
// logger's output, which is the common case.
func TestProjectRulesReachIntoALogBody(t *testing.T) {
	rules := rulesFor(t, `{"mask_keys":["user.email"],"allow_values":["`+testCardNumber+`"]}`)
	body := mapValue(
		stringAttribute("user.email", "ada@example.test"),
		stringAttribute("order.reference", testCardNumber),
	)

	rendered, count := maskBody(t, rules, body)
	if strings.Contains(rendered, "ada@example.test") {
		t.Errorf("body is %q, want the project's masked key gone", rendered)
	}
	if !strings.Contains(rendered, testCardNumber) {
		t.Errorf("body is %q, want the allowlisted reference to survive", rendered)
	}
	if count != 1 {
		t.Errorf("MaskResourceLogs() reported %d masked values, want 1", count)
	}
}

// The key rules have to reach inside a structured value even for a project that
// switched the value rules off — otherwise disabling card matching silently
// turns off masking for every nested credential too.
func TestKeyRulesReachIntoStructuresWithTheCardRuleOff(t *testing.T) {
	rules := rulesFor(t, `{"disable":["card_numbers"]}`)
	body := mapValue(&commonpb.KeyValue{
		Key:   "request",
		Value: mapValue(stringAttribute("password", "hunter2")),
	})

	rendered, count := maskBody(t, rules, body)
	if strings.Contains(rendered, "hunter2") {
		t.Errorf("body is %q, want the nested password masked", rendered)
	}
	if count != 1 {
		t.Errorf("MaskResourceLogs() reported %d masked values, want 1", count)
	}
}
