// Package browserdoor drives door 3 — the browser door — the way a page does.
//
// telemetrygen cannot produce this traffic. A web vital is an OTLP log record
// carrying an event name and a fixed set of `browser.web_vital.*` attributes,
// and the door authenticates on a public site key plus an Origin header rather
// than on an ingest key. So the payloads are built here, shaped to match
// packages/browser-sdk exactly: a generator that sent a slightly different
// record would measure a door no customer reaches.
//
// It also fills the RUM and Errors screens on a dev stack, which nothing else
// does — the seeded tenant has telemetry from every other door and none from
// this one until `just load` runs.
package browserdoor

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"strconv"
	"time"
)

// The SDK's own spellings, from packages/browser-sdk/src/semconv.ts and
// constants.ts. Copied rather than shared because the two sides are different
// languages; a drifted key here writes an empty column rather than failing, so
// the SDK is the source and this is the copy.
const (
	eventWebVital  = "browser.web_vital"
	eventException = "exception"

	attrVitalName      = "browser.web_vital.name"
	attrVitalValue     = "browser.web_vital.value"
	attrVitalDelta     = "browser.web_vital.delta"
	attrVitalID        = "browser.web_vital.id"
	attrVitalRating    = "browser.web_vital.rating"
	attrNavigationType = "browser.web_vital.navigation_type"

	attrSessionID = "session.id"
	attrURLFull   = "url.full"
	attrURLPath   = "url.path"

	attrExceptionType    = "exception.type"
	attrExceptionMessage = "exception.message"
	attrExceptionStack   = "exception.stacktrace"

	sdkName    = "@nexovue/browser-sdk"
	sdkVersion = "0.1.0"
)

// severityInfo and severityError are OTLP's numbers for the two levels the SDK
// emits; a vital is INFO and a thrown error is ERROR.
const (
	severityInfo  = 9
	severityError = 17
)

// The five vitals and where a healthy page sits on each, so a dev stack shows
// plausible numbers rather than uniform noise. Milliseconds except cls, which is
// a unitless ratio.
var vitalShapes = []struct {
	name   string
	median float64
	spread float64
}{
	{"lcp", 1800, 900},
	{"inp", 120, 90},
	{"cls", 0.05, 0.06},
	{"fcp", 900, 400},
	{"ttfb", 220, 160},
}

// The pages a simulated visitor loads. A handful rather than one, so `url_path`
// has something to group by and the rollup's cardinality is exercised.
var pagePaths = []string{"/", "/products", "/products/checkout", "/account", "/search"}

type keyValue struct {
	Key   string   `json:"key"`
	Value anyValue `json:"value"`
}

type anyValue struct {
	String *string  `json:"stringValue,omitempty"`
	Double *float64 `json:"doubleValue,omitempty"`
	Bool   *bool    `json:"boolValue,omitempty"`
}

func str(key, value string) keyValue {
	return keyValue{Key: key, Value: anyValue{String: &value}}
}

func num(key string, value float64) keyValue {
	return keyValue{Key: key, Value: anyValue{Double: &value}}
}

func boolean(key string, value bool) keyValue {
	return keyValue{Key: key, Value: anyValue{Bool: &value}}
}

type logRecord struct {
	TimeUnixNano         string     `json:"timeUnixNano"`
	ObservedTimeUnixNano string     `json:"observedTimeUnixNano"`
	SeverityNumber       int        `json:"severityNumber"`
	SeverityText         string     `json:"severityText"`
	EventName            string     `json:"eventName"`
	Attributes           []keyValue `json:"attributes"`
}

// visitor is one simulated browser: a session id it keeps, and a device class it
// reports on every export.
type visitor struct {
	sessionID string
	mobile    bool
	origin    string
}

func newVisitor(random *rand.Rand, origin string) visitor {
	return visitor{
		sessionID: randomHex(random, 16),
		// Roughly the real split, so `device_class` has both values in it.
		mobile: random.Float64() < 0.55,
		origin: origin,
	}
}

// resource is what every export from this visitor carries.
//
// `browser.mobile` sits here and not on the record, which is where the SDK puts
// it and where the worker reads it from — a generator that put it on the record
// would leave `device_class` empty and prove nothing.
func (v visitor) resource(serviceName, release string) []keyValue {
	return []keyValue{
		str("service.name", serviceName),
		str("service.version", release),
		str("telemetry.sdk.name", sdkName),
		str("telemetry.sdk.language", "webjs"),
		str("telemetry.sdk.version", sdkVersion),
		str("user_agent.original", v.userAgent()),
		str("browser.language", "en-GB"),
		str("browser.platform", v.platform()),
		boolean("browser.mobile", v.mobile),
	}
}

func (v visitor) userAgent() string {
	if v.mobile {
		return "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 " +
			"(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
	}
	return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
		"(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
}

func (v visitor) platform() string {
	if v.mobile {
		return "iOS"
	}
	return "macOS"
}

// pageContext is what the SDK stamps on every record: who, and where.
func (v visitor) pageContext(path string) []keyValue {
	return []keyValue{
		str(attrSessionID, v.sessionID),
		str(attrURLFull, v.origin+path),
		str(attrURLPath, path),
	}
}

// vitalsFor is one page load's worth of readings.
//
// cls is omitted for a mobile Safari visitor, which is not a shortcut: layout
// shift is Chromium-only, so a generator reporting it from every browser would
// make the screen's "no readings" state unreachable and untested.
func (v visitor) vitalsFor(random *rand.Rand, path string, at time.Time) []logRecord {
	records := make([]logRecord, 0, len(vitalShapes))
	for _, shape := range vitalShapes {
		if shape.name == "cls" && v.mobile {
			continue
		}
		value := positive(shape.median + random.NormFloat64()*shape.spread)
		attributes := []keyValue{
			str(attrVitalName, shape.name),
			num(attrVitalValue, value),
			num(attrVitalDelta, value),
			str(attrVitalID, fmt.Sprintf("v6-%d-%s", at.UnixMilli(), randomHex(random, 6))),
			str(attrVitalRating, ratingOf(shape.name, value)),
			str(attrNavigationType, "navigate"),
		}
		records = append(records, logRecord{
			TimeUnixNano:         unixNano(at),
			ObservedTimeUnixNano: unixNano(at),
			SeverityNumber:       severityInfo,
			SeverityText:         "INFO",
			EventName:            eventWebVital,
			Attributes:           append(attributes, v.pageContext(path)...),
		})
	}
	return records
}

// errorFor is one thrown exception, shaped as the SDK reports it: a minified
// stack, because symbolication is what reads it back.
func (v visitor) errorFor(random *rand.Rand, path string, at time.Time, release string) logRecord {
	kind := errorKinds[random.IntN(len(errorKinds))]
	stack := fmt.Sprintf(
		"%s: %s\n    at t (%s/static/js/main.%s.js:1:%d)\n    at n (%s/static/js/main.%s.js:1:%d)",
		kind.exception, kind.message,
		v.origin, release, 40000+random.IntN(50000),
		v.origin, release, 40000+random.IntN(50000),
	)
	return logRecord{
		TimeUnixNano:         unixNano(at),
		ObservedTimeUnixNano: unixNano(at),
		SeverityNumber:       severityError,
		SeverityText:         "ERROR",
		EventName:            eventException,
		Attributes: append([]keyValue{
			str(attrExceptionType, kind.exception),
			str(attrExceptionMessage, kind.message),
			str(attrExceptionStack, stack),
		}, v.pageContext(path)...),
	}
}

// A handful of distinct exceptions, so the Errors screen has more than one group
// and its grouping is exercised rather than assumed.
var errorKinds = []struct {
	exception string
	message   string
}{
	{"TypeError", "Cannot read properties of undefined (reading 'total')"},
	{"TypeError", "n.checkout is not a function"},
	{"RangeError", "Invalid array length"},
	{"NetworkError", "Failed to fetch"},
}

// logsExport is the body the door reads, matching the SDK's logsExport exactly.
func logsExport(resource []keyValue, records []logRecord) ([]byte, error) {
	return json.Marshal(map[string]any{
		"resourceLogs": []any{map[string]any{
			"resource": map[string]any{"attributes": resource},
			"scopeLogs": []any{map[string]any{
				"scope":      map[string]any{"name": sdkName, "version": sdkVersion},
				"logRecords": records,
			}},
		}},
	})
}

// ratingOf is web-vitals' own verdict for a value, at its own thresholds. The
// SDK sends the library's rating, so a generator inventing one would write a
// column that disagrees with the value beside it.
func ratingOf(name string, value float64) string {
	good, poor := thresholds(name)
	switch {
	case value <= good:
		return "good"
	case value <= poor:
		return "needs-improvement"
	default:
		return "poor"
	}
}

func thresholds(name string) (good, poor float64) {
	switch name {
	case "lcp":
		return 2500, 4000
	case "inp":
		return 200, 500
	case "cls":
		return 0.1, 0.25
	case "fcp":
		return 1800, 3000
	default: // ttfb
		return 800, 1800
	}
}

// positive keeps a normally distributed sample on the right side of zero. A
// negative duration is not a slow page, it is a broken generator.
func positive(value float64) float64 {
	if value < 0 {
		return 0
	}
	return value
}

func unixNano(at time.Time) string {
	return strconv.FormatInt(at.UnixNano(), 10)
}

func randomHex(random *rand.Rand, bytes int) string {
	buffer := make([]byte, bytes)
	for i := range buffer {
		buffer[i] = byte(random.UintN(256))
	}
	return hex.EncodeToString(buffer)
}
