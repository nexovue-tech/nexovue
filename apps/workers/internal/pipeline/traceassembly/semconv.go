package traceassembly

import (
	"strconv"
	"strings"
)

// OpenTelemetry's HTTP conventions stabilized in semconv 1.23.0, renaming the
// attributes the promoted columns are built from. Customers run SDKs from both
// sides of that line, and an SDK migrating with OTEL_SEMCONV_STABILITY_OPT_IN
// emits both spellings at once — so the envelope's semconv_version decides
// which spelling is authoritative rather than the flattener guessing.
const (
	attrHTTPMethodStable = "http.request.method"
	attrHTTPMethodLegacy = "http.method"
	attrHTTPStatusStable = "http.response.status_code"
	attrHTTPStatusLegacy = "http.status_code"

	// Unchanged by stabilization; named once so both tables stay symmetrical.
	attrHTTPRoute = "http.route"
)

// httpStableSince is the first semantic-convention release whose HTTP
// attributes use the stable names.
var httpStableSince = semconvVersion{major: 1, minor: 23}

// httpAttributeNames is one generation's spelling of the promoted HTTP columns.
type httpAttributeNames struct {
	method string
	route  string
	status string
}

var (
	stableHTTPNames = httpAttributeNames{attrHTTPMethodStable, attrHTTPRoute, attrHTTPStatusStable}
	legacyHTTPNames = httpAttributeNames{attrHTTPMethodLegacy, attrHTTPRoute, attrHTTPStatusLegacy}
)

// httpNamesFor returns the spellings to try, most authoritative first. Both
// generations are always tried: the version decides precedence, never whether a
// payload is looked at, so a mislabelled envelope still fills its columns.
func httpNamesFor(semconv string) [2]httpAttributeNames {
	parsed, ok := parseSemconvVersion(semconv)
	if ok && parsed.before(httpStableSince) {
		return [2]httpAttributeNames{legacyHTTPNames, stableHTTPNames}
	}
	// An absent or unparseable version means a recent SDK often enough that
	// stable-first is the better guess, and the fallback covers the rest.
	return [2]httpAttributeNames{stableHTTPNames, legacyHTTPNames}
}

// semconvVersion is a semantic-convention release, compared on major and minor
// only — patch releases never rename an attribute.
type semconvVersion struct {
	major int
	minor int
}

func (v semconvVersion) before(other semconvVersion) bool {
	if v.major != other.major {
		return v.major < other.major
	}
	return v.minor < other.minor
}

// parseSemconvVersion reads a "1.23.0" or "1.23" version, tolerating the
// "v" prefix some emitters add. It reports false on anything else.
func parseSemconvVersion(raw string) (semconvVersion, bool) {
	trimmed := strings.TrimPrefix(strings.TrimSpace(raw), "v")
	if trimmed == "" {
		return semconvVersion{}, false
	}

	parts := strings.SplitN(trimmed, ".", 3)
	if len(parts) < 2 {
		return semconvVersion{}, false
	}
	major, err := strconv.Atoi(parts[0])
	if err != nil {
		return semconvVersion{}, false
	}
	minor, err := strconv.Atoi(parts[1])
	if err != nil {
		return semconvVersion{}, false
	}
	return semconvVersion{major: major, minor: minor}, true
}
