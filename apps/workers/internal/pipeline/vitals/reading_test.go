package vitals

import (
	"testing"
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
)

func stringAttr(key, value string) *commonpb.KeyValue {
	return &commonpb.KeyValue{
		Key:   key,
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}},
	}
}

func doubleAttr(key string, value float64) *commonpb.KeyValue {
	return &commonpb.KeyValue{
		Key:   key,
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_DoubleValue{DoubleValue: value}},
	}
}

func boolAttr(key string, value bool) *commonpb.KeyValue {
	return &commonpb.KeyValue{
		Key:   key,
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_BoolValue{BoolValue: value}},
	}
}

func testMarks() Marks {
	at := time.Date(2026, 9, 6, 11, 30, 20, 0, time.UTC)
	return Marks{
		Timestamp:            at,
		ReceivedAt:           at,
		RetentionDays:        30,
		DerivedRetentionDays: 395,
		TenantID:             "tenant-a",
		ProjectID:            "project-a",
		DeviceClass:          DeviceClassMobile,
	}
}

// vitalRecord is one reading shaped exactly as packages/browser-sdk emits it.
func vitalRecord() *logspb.LogRecord {
	return &logspb.LogRecord{
		EventName: EventName,
		Attributes: []*commonpb.KeyValue{
			stringAttr(attrVitalName, "lcp"),
			doubleAttr(attrVitalValue, 1842.5),
			doubleAttr(attrVitalDelta, 1842.5),
			stringAttr(attrVitalID, "v6-1788626299167-1234567890123"),
			stringAttr(attrVitalRating, "needs-improvement"),
			stringAttr(attrNavigationType, "navigate"),
			stringAttr(attrSessionID, "session-1"),
			stringAttr(attrURLPath, "/checkout"),
		},
	}
}

func TestReadingPromotesEveryColumnTheSDKSends(t *testing.T) {
	reading, isVital := ReadingFrom(vitalRecord(), testMarks())
	if !isVital {
		t.Fatal("a record carrying the SDK's own event name was not read as a vital")
	}

	if reading.VitalName != "lcp" {
		t.Errorf("vital_name = %q, want lcp", reading.VitalName)
	}
	if reading.Value != 1842.5 {
		t.Errorf("value = %v, want 1842.5", reading.Value)
	}
	if reading.Rating != "needs-improvement" {
		t.Errorf("rating = %q, want needs-improvement", reading.Rating)
	}
	if reading.NavigationType != "navigate" {
		t.Errorf("navigation_type = %q, want navigate", reading.NavigationType)
	}
	if reading.SessionID != "session-1" {
		t.Errorf("session_id = %q, want session-1", reading.SessionID)
	}
	if reading.URLPath != "/checkout" {
		t.Errorf("url_path = %q, want /checkout", reading.URLPath)
	}
	if reading.DeviceClass != DeviceClassMobile {
		t.Errorf("device_class = %q, want %q", reading.DeviceClass, DeviceClassMobile)
	}
	if reading.TenantID != "tenant-a" || reading.ProjectID != "project-a" {
		t.Errorf("ownership = (%q, %q), want (tenant-a, project-a)",
			reading.TenantID, reading.ProjectID)
	}
	if reading.Country != "" {
		t.Errorf("country = %q, want empty: nothing resolves the client address", reading.Country)
	}
}

// The breakdown groups by `route` and falls back to `url_path`, so a template
// dropped here means a group per order id for the customer who configured one.
// Unlike browser.mobile it is a RECORD attribute — `pagecontext.ts` puts it on
// every vital and error record — so it is read from the record's own list.
func TestTheRouteTemplateIsReadFromTheRecordsOwnAttributes(t *testing.T) {
	const template = "/cart/checkout/{orderId}"
	record := vitalRecord()
	record.Attributes = append(record.Attributes, stringAttr(attrHTTPRoute, template))

	reading, isVital := ReadingFrom(record, testMarks())
	if !isVital {
		t.Fatal("a record carrying the SDK's own event name was not read as a vital")
	}
	if reading.Route != template {
		t.Errorf("route = %q, want %q", reading.Route, template)
	}
	// Both are stored: the path is what the fallback groups by, and it is also
	// how a reader tells "this page" from "this one URL".
	if reading.URLPath != "/checkout" {
		t.Errorf("url_path = %q, want /checkout beside the route", reading.URLPath)
	}
}

// A customer who configured no route must still get a breakdown, grouped by the
// path — and the column must be empty rather than a template guessed from it.
func TestAReadingWithoutARouteCarriesAnEmptyOneRatherThanAGuess(t *testing.T) {
	record := vitalRecord()
	record.Attributes = append(record.Attributes,
		stringAttr(attrURLPath, "/cart/checkout/978ac453-aa9f-11f1-91c3-1e31e47ab720"))

	reading, _ := ReadingFrom(record, testMarks())
	if reading.Route != "" {
		t.Errorf("route = %q, want empty: a template is never derived from a path", reading.Route)
	}
}

func TestRecordsThatAreNotReadingsAreRefused(t *testing.T) {
	withoutName := vitalRecord()
	withoutName.Attributes = withoutName.Attributes[1:]

	noValue := vitalRecord()
	noValue.Attributes = []*commonpb.KeyValue{stringAttr(attrVitalName, "lcp")}

	cases := map[string]*logspb.LogRecord{
		"an ordinary log record":  {},
		"an error record":         {EventName: "exception"},
		"a vital with no name":    withoutName,
		"a vital with no reading": noValue,
	}
	for name, record := range cases {
		t.Run(name, func(t *testing.T) {
			if _, isVital := ReadingFrom(record, testMarks()); isVital {
				t.Error("was promoted to a reading, which would put an empty row in the rollup")
			}
		})
	}
}

// A vital whose value lands on a whole number is encoded as an integer by any
// exporter that writes JSON. Refusing those would silently drop every CLS of 0.
func TestAnIntegerValuedReadingIsStillARreading(t *testing.T) {
	record := vitalRecord()
	record.Attributes = []*commonpb.KeyValue{
		stringAttr(attrVitalName, "cls"),
		{
			Key:   attrVitalValue,
			Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: 0}},
		},
	}

	reading, isVital := ReadingFrom(record, testMarks())
	if !isVital {
		t.Fatal("an integer-encoded value was refused")
	}
	if reading.Value != 0 {
		t.Errorf("value = %v, want 0", reading.Value)
	}
	// Absent, so it falls back to the value rather than to an arbitrary zero.
	if reading.Delta != 0 {
		t.Errorf("delta = %v, want 0", reading.Delta)
	}
}

func TestDeviceClassComesFromTheResourceAndNotFromTheRecord(t *testing.T) {
	// The defect this catches was shipped and found in a real browser: the SDK
	// puts browser.mobile on the RESOURCE, so a device class read off a record's
	// own attributes is empty for every reading ever written. A column nothing
	// writes to looks exactly like a column nothing has filled yet.
	record := vitalRecord()
	record.Attributes = append(record.Attributes, boolAttr(AttrMobile, true))

	marks := testMarks()
	marks.DeviceClass = ""
	reading, _ := ReadingFrom(record, marks)

	if reading.DeviceClass != "" {
		t.Errorf("device_class = %q from a record attribute; it is a resource attribute",
			reading.DeviceClass)
	}
}

func TestDeviceClassOfTheResource(t *testing.T) {
	cases := map[string]struct {
		attributes []*commonpb.KeyValue
		want       string
	}{
		"a phone":                {[]*commonpb.KeyValue{boolAttr(AttrMobile, true)}, DeviceClassMobile},
		"a desktop":              {[]*commonpb.KeyValue{boolAttr(AttrMobile, false)}, DeviceClassDesktop},
		"a browser that said no": {nil, ""},
		// A string "true" is a different attribute shape, and reading it as a
		// boolean would invent agreement the sender never expressed.
		"the wrong shape": {[]*commonpb.KeyValue{stringAttr(AttrMobile, "true")}, ""},
	}
	for name, test := range cases {
		t.Run(name, func(t *testing.T) {
			if got := DeviceClassOf(test.attributes); got != test.want {
				t.Errorf("device class = %q, want %q", got, test.want)
			}
		})
	}
}
