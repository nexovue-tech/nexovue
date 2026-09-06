package browserdoor

import (
	"encoding/json"
	"math/rand/v2"
	"testing"
	"time"
)

func testRandom() *rand.Rand { return rand.New(rand.NewPCG(1, 2)) }

func testTime() time.Time { return time.Date(2026, 9, 6, 13, 30, 0, 0, time.UTC) }

func attributeMap(attributes []keyValue) map[string]anyValue {
	found := make(map[string]anyValue, len(attributes))
	for _, attribute := range attributes {
		found[attribute.Key] = attribute.Value
	}
	return found
}

// The worker promotes a reading by reading these exact keys off the record. A
// drifted spelling does not fail anything — it writes an empty column — so the
// names are asserted literally here, spelled as packages/browser-sdk writes them.
func TestAVitalCarriesEveryAttributeTheWorkerPromotes(t *testing.T) {
	visitor := newVisitor(testRandom(), "https://shop.example.com")
	records := visitor.vitalsFor(testRandom(), "/products", testTime())

	if len(records) == 0 {
		t.Fatal("no vitals were produced")
	}
	record := records[0]
	if record.EventName != "browser.web_vital" {
		t.Errorf("event name = %q, want browser.web_vital", record.EventName)
	}

	attributes := attributeMap(record.Attributes)
	for _, key := range []string{
		"browser.web_vital.name",
		"browser.web_vital.value",
		"browser.web_vital.delta",
		"browser.web_vital.id",
		"browser.web_vital.rating",
		"browser.web_vital.navigation_type",
		"session.id",
		"url.path",
	} {
		if _, present := attributes[key]; !present {
			t.Errorf("a vital record carries no %q", key)
		}
	}
}

// The defect this guards against was shipped once: browser.mobile is a resource
// attribute, and a generator putting it on the record would leave device_class
// empty while appearing to exercise it.
func TestBrowserMobileIsOnTheResourceAndNotOnTheRecord(t *testing.T) {
	visitor := newVisitor(testRandom(), "https://shop.example.com")

	resource := attributeMap(visitor.resource("shop", "2026.09.06-1"))
	if _, present := resource["browser.mobile"]; !present {
		t.Error("the resource carries no browser.mobile, so device_class stays empty")
	}

	for _, record := range visitor.vitalsFor(testRandom(), "/", testTime()) {
		if _, present := attributeMap(record.Attributes)["browser.mobile"]; present {
			t.Error("browser.mobile is on the record; the worker reads it off the resource")
		}
	}
}

// CLS is Chromium-only. Reporting it from a simulated Safari would make the
// screen's "no readings in this window" state unreachable, and that state is
// the one a real customer on Safari sees.
func TestALayoutShiftIsNotReportedFromABrowserThatCannotMeasureIt(t *testing.T) {
	mobile := visitor{sessionID: "s", mobile: true, origin: "https://shop.example.com"}
	desktop := visitor{sessionID: "s", mobile: false, origin: "https://shop.example.com"}

	if names := vitalNames(mobile.vitalsFor(testRandom(), "/", testTime())); contains(names, "cls") {
		t.Errorf("a mobile visitor reported cls: %v", names)
	}
	if names := vitalNames(desktop.vitalsFor(testRandom(), "/", testTime())); !contains(names, "cls") {
		t.Errorf("a desktop visitor reported no cls: %v", names)
	}
}

func vitalNames(records []logRecord) []string {
	names := make([]string, 0, len(records))
	for _, record := range records {
		if value, ok := attributeMap(record.Attributes)["browser.web_vital.name"]; ok {
			names = append(names, *value.String)
		}
	}
	return names
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

// The rating travels beside the value, so the two must agree. web-vitals' own
// thresholds decide, and a generator that invented its own would write a column
// disagreeing with the number next to it.
func TestTheRatingAgreesWithTheValueItTravelsWith(t *testing.T) {
	cases := []struct {
		vital string
		value float64
		want  string
	}{
		{"lcp", 2500, "good"},
		{"lcp", 2500.1, "needs-improvement"},
		{"lcp", 4000.1, "poor"},
		{"inp", 200, "good"},
		{"cls", 0.1, "good"},
		{"cls", 0.26, "poor"},
		{"ttfb", 800, "good"},
		{"fcp", 3000.1, "poor"},
	}
	for _, test := range cases {
		if got := ratingOf(test.vital, test.value); got != test.want {
			t.Errorf("%s at %v rated %q, want %q", test.vital, test.value, got, test.want)
		}
	}
}

func TestAnErrorCarriesAMinifiedStackAndItsOwnPosition(t *testing.T) {
	visitor := newVisitor(testRandom(), "https://shop.example.com")

	record := visitor.errorFor(testRandom(), "/products/checkout", testTime(), "2026.09.06-1")

	if record.EventName != "exception" {
		t.Errorf("event name = %q, want exception", record.EventName)
	}
	attributes := attributeMap(record.Attributes)
	stack, present := attributes["exception.stacktrace"]
	if !present || stack.String == nil || *stack.String == "" {
		t.Fatal("an error carries no stacktrace, so nothing on the Errors screen can symbolicate")
	}
	// A minified position is the point: it is what a source map reads back.
	if !containsSubstring(*stack.String, "/static/js/main.") {
		t.Errorf("the stack names no minified bundle: %q", *stack.String)
	}
}

func containsSubstring(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (func() bool {
		for i := 0; i+len(needle) <= len(haystack); i++ {
			if haystack[i:i+len(needle)] == needle {
				return true
			}
		}
		return false
	})()
}

// The release is on the resource as service.version, which is where the API
// reads a release from when it matches a source map.
func TestTheResourceCarriesTheReleaseASourceMapIsMatchedOn(t *testing.T) {
	visitor := newVisitor(testRandom(), "https://shop.example.com")

	resource := attributeMap(visitor.resource("shop", "2026.09.06-1"))

	version, present := resource["service.version"]
	if !present || version.String == nil || *version.String != "2026.09.06-1" {
		t.Error("the resource carries no service.version, so no stack can be symbolicated")
	}
}

// The body has to be the shape the door's JSON decoder expects; a wrong nesting
// is a 400 for every export and a run of pure refusals.
func TestTheExportIsShapedTheWayTheDoorReadsIt(t *testing.T) {
	visitor := newVisitor(testRandom(), "https://shop.example.com")
	body, err := logsExport(
		visitor.resource("shop", "1"), visitor.vitalsFor(testRandom(), "/", testTime()))
	if err != nil {
		t.Fatalf("logsExport: %v", err)
	}

	var decoded struct {
		ResourceLogs []struct {
			Resource struct {
				Attributes []keyValue `json:"attributes"`
			} `json:"resource"`
			ScopeLogs []struct {
				LogRecords []logRecord `json:"logRecords"`
			} `json:"scopeLogs"`
		} `json:"resourceLogs"`
	}
	if err := json.Unmarshal(body, &decoded); err != nil {
		t.Fatalf("the export is not readable JSON: %v", err)
	}
	if len(decoded.ResourceLogs) != 1 {
		t.Fatalf("resourceLogs = %d, want 1", len(decoded.ResourceLogs))
	}
	if len(decoded.ResourceLogs[0].ScopeLogs[0].LogRecords) == 0 {
		t.Error("the export carries no log records")
	}
	if len(decoded.ResourceLogs[0].Resource.Attributes) == 0 {
		t.Error("the export carries no resource, so device_class and release are lost")
	}
}

func TestAValueNeverGoesNegativeHoweverTheSampleFalls(t *testing.T) {
	// A normal distribution around 120ms with a 90ms spread produces negatives.
	// A negative duration is not a fast page, it is a broken generator.
	random := rand.New(rand.NewPCG(99, 7))
	visitor := visitor{sessionID: "s", mobile: false, origin: "https://shop.example.com"}

	for range 200 {
		for _, record := range visitor.vitalsFor(random, "/", testTime()) {
			value := attributeMap(record.Attributes)["browser.web_vital.value"]
			if value.Double == nil || *value.Double < 0 {
				t.Fatalf("a vital reported %v", value.Double)
			}
		}
	}
}
