// Package vitals promotes the Core Web Vitals out of the browser door's log
// records into their own table and per-minute rollup.
//
// The readings arrive as ordinary OTLP log records — the browser SDK emits each
// one with event name `browser.web_vital` — and are written to `logs` like any
// other record. This package is the second destination, not a replacement: it
// copies the five attributes a RUM screen filters and aggregates on into
// `web_vitals`, for the reason migration 000030 gives. Answering "p75 LCP by
// page" out of `logs` means parsing the attributes JSON of every row in the
// window, a cost that grows with the customer's log volume rather than with the
// question.
//
// Extraction happens inside the log-writing pipeline rather than in a consumer
// of its own. The records are already decoded and already masked there, and a
// second consumer group over the same topic would pay both costs again and need
// a watermark of its own to stay consistent with the first.
package vitals

import (
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// EventName is what the browser SDK stamps on a vital record; `constants.ts`
// holds the same literal. A record without it is not a vital reading.
const EventName = "browser.web_vital"

// The attributes a vital record carries, spelled as packages/browser-sdk/src/
// semconv.ts writes them. A drifted spelling here does not fail — it silently
// stores an empty column — so the SDK's names are the source and these are the
// copy.
const (
	attrVitalName      = "browser.web_vital.name"
	attrVitalValue     = "browser.web_vital.value"
	attrVitalDelta     = "browser.web_vital.delta"
	attrVitalID        = "browser.web_vital.id"
	attrVitalRating    = "browser.web_vital.rating"
	attrNavigationType = "browser.web_vital.navigation_type"

	attrSessionID = "session.id"
	attrURLPath   = "url.path"
	// A RECORD attribute, unlike browser.mobile below: `pagecontext.ts` puts it
	// on every vital and error record, not only on the page-view span.
	attrHTTPRoute = "http.route"
)

// AttrMobile is a RESOURCE attribute, not a record one. `resource.ts` puts the
// browser.* facts on the resource because they describe the visitor's browser
// rather than any one reading — so a device class read off a record's own
// attributes would be empty for every reading ever written.
const AttrMobile = "browser.mobile"

// The device classes this system can tell apart, which is two. `browser.mobile`
// is a boolean, so a tablet is indistinguishable from a phone and neither is
// distinguishable from a small laptop; the column says what the browser
// claimed, not what the hardware was.
const (
	DeviceClassMobile  = "mobile"
	DeviceClassDesktop = "desktop"
)

// Reading is one vital measurement, in the shape `web_vitals` stores.
type Reading struct {
	Timestamp time.Time
	// ReceivedAt is our own clock, and carries both the partition key and the
	// TTL. A visitor's device clock can be set to any year at all, so the
	// browser's timestamp is stored but never used to expire or place a row.
	ReceivedAt    time.Time
	RetentionDays uint16
	// DerivedRetentionDays applies to the rollup rather than to this row. It
	// travels with the reading so the two writers need only one input.
	DerivedRetentionDays uint16

	TenantID  string
	ProjectID string
	SessionID string

	// VitalName is the SDK's own lower-case spelling: cls, fcp, inp, lcp, ttfb.
	VitalName string
	// VitalID identifies this reading, and is what weights it in the rollup's
	// bounded sample. web-vitals mints one per metric per page load, so it is
	// the only thing that distinguishes two readings a browser reported in the
	// same millisecond.
	VitalID string
	// Value is milliseconds for fcp, inp, lcp and ttfb, and a unitless ratio
	// for cls. Passed through exactly as the browser reported it.
	Value          float64
	Delta          float64
	Rating         string
	NavigationType string

	URLPath string
	// Route is the customer's own route template — `/products/{id}` — and is
	// what a page breakdown groups by. Empty when they configured none, and
	// never derived from the path: a wrong template would be invisible.
	Route       string
	DeviceClass string
	// Country has no producer: there is no GeoIP anywhere in this system, so
	// nothing resolves the client address. The column exists so that filling it
	// later is a write rather than a migration.
	Country string

	// Attrs is everything the record carried, including the browser.* facts and
	// the vital's own id, kept so a question the promoted columns cannot answer
	// does not require going back to `logs`.
	Attrs string
}

// ReadingFrom promotes one log record, reporting whether it was a vital at all.
//
// A record claiming the event name but carrying no vital name or no value is
// not a reading and is refused rather than stored as an empty row: the rollup
// would then hold a p75 over readings that measured nothing, which no screen
// could tell apart from a real one.
func ReadingFrom(record *logspb.LogRecord, marks Marks) (Reading, bool) {
	if record.GetEventName() != EventName {
		return Reading{}, false
	}
	attributes := record.GetAttributes()

	name, named := otlpvalue.String(attributes, attrVitalName)
	value, measured := otlpvalue.Float64(attributes, attrVitalValue)
	if !named || name == "" || !measured {
		return Reading{}, false
	}

	// The delta defaults to the value, which is what it equals on a metric's
	// first report; a reading without one is not worth refusing over.
	delta, hasDelta := otlpvalue.Float64(attributes, attrVitalDelta)
	if !hasDelta {
		delta = value
	}

	return Reading{
		Timestamp:            marks.Timestamp,
		ReceivedAt:           marks.ReceivedAt,
		RetentionDays:        marks.RetentionDays,
		DerivedRetentionDays: marks.DerivedRetentionDays,
		TenantID:             marks.TenantID,
		ProjectID:            marks.ProjectID,
		SessionID:            otlpvalue.StringOrEmpty(attributes, attrSessionID),
		VitalName:            name,
		VitalID:              otlpvalue.StringOrEmpty(attributes, attrVitalID),
		Value:                value,
		Delta:                delta,
		Rating:               otlpvalue.StringOrEmpty(attributes, attrVitalRating),
		NavigationType:       otlpvalue.StringOrEmpty(attributes, attrNavigationType),
		URLPath:              otlpvalue.StringOrEmpty(attributes, attrURLPath),
		Route:                otlpvalue.StringOrEmpty(attributes, attrHTTPRoute),
		DeviceClass:          marks.DeviceClass,
		Attrs:                otlpvalue.AttributesJSON(attributes),
	}, true
}

// Marks are the values a reading takes from its message rather than from its
// own attributes: whose it is, when we received it, and how long it is kept.
type Marks struct {
	Timestamp            time.Time
	ReceivedAt           time.Time
	RetentionDays        uint16
	DerivedRetentionDays uint16
	TenantID             string
	ProjectID            string
	// DeviceClass is derived from the resource, so it is passed in rather than
	// read here — see AttrMobile.
	DeviceClass string
}

// DeviceClassOf reads the one device fact the browser reports, from the resource
// attributes that carry it. Absent stays empty rather than defaulting to
// desktop: an unfilled column is visibly unfilled, where a wrong default is
// indistinguishable from a measurement.
func DeviceClassOf(attributes []*commonpb.KeyValue) string {
	mobile, reported := otlpvalue.Bool(attributes, AttrMobile)
	switch {
	case !reported:
		return ""
	case mobile:
		return DeviceClassMobile
	default:
		return DeviceClassDesktop
	}
}
