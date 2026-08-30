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

// OpenTelemetry reshaped its RPC conventions in semconv 1.39.0, and unlike HTTP
// none of it is stable yet — the whole group is release candidate as of 1.44.0.
// Three things moved: `rpc.system` became `rpc.system.name`, `rpc.service` was
// folded into a now fully-qualified `rpc.method`, and the per-framework integer
// status codes became one string `rpc.response.status_code`.
//
// Unlike HTTP, the envelope's semconv_version is not consulted: each generation
// either has a key of its own or a distinguishable shape, so the payload
// disambiguates itself and a mislabelled envelope costs nothing.
const (
	attrRPCSystemCurrent = "rpc.system.name"
	attrRPCSystemLegacy  = "rpc.system"

	// One key, two meanings: fully qualified from 1.39.0, the bare method name
	// before it. The presence of rpc.service is what tells the two apart.
	attrRPCMethod        = "rpc.method"
	attrRPCServiceLegacy = "rpc.service"

	attrRPCStatusCurrent = "rpc.response.status_code"

	// The three per-framework codes 1.39.0 replaced with the one above.
	attrRPCStatusGRPCLegacy    = "rpc.grpc.status_code"
	attrRPCStatusConnectLegacy = "rpc.connect_rpc.error_code"
	attrRPCStatusJSONRPCLegacy = "rpc.jsonrpc.error_code"
)

// rpcSystemRenames are the two `rpc.system` values 1.39.0 respelled. Applying
// them here means one stored vocabulary, so a filter matches a fleet whose SDKs
// are mid-migration rather than half of it.
var rpcSystemRenames = map[string]string{
	"apache_dubbo": "dubbo",
	"connect_rpc":  "connectrpc",
}

// canonicalRPCSystem is the current spelling of an RPC system name. A value
// outside the enum is a custom system and is kept as it arrived.
func canonicalRPCSystem(system string) string {
	if renamed, ok := rpcSystemRenames[system]; ok {
		return renamed
	}
	return system
}

// qualifiedRPCMethod is the fully-qualified method name 1.39.0 made
// `rpc.method` carry, rebuilt from the legacy pair when that is what arrived.
//
// An SDK in `rpc/dup` mode emits both generations, and they collide on this one
// key; the prefix check is what stops an already-qualified name being qualified
// twice.
func qualifiedRPCMethod(service, method string) string {
	if service == "" || method == "" {
		return method
	}
	if strings.HasPrefix(method, service+rpcMethodSeparator) {
		return method
	}
	return service + rpcMethodSeparator + method
}

// rpcMethodSeparator joins service and method in a fully-qualified name, as in
// `com.example.ExampleService/exampleMethod`.
const rpcMethodSeparator = "/"

// grpcStatusNames is the gRPC status enum indexed by its code. 1.39.0 turned
// the integer `rpc.grpc.status_code` into the string `rpc.response.status_code`
// — 0 became "OK" — so promoting the legacy integer through this table puts
// both generations in one vocabulary.
var grpcStatusNames = [...]string{
	"OK", "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED",
	"NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED",
	"FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED",
	"INTERNAL", "UNAVAILABLE", "DATA_LOSS", "UNAUTHENTICATED",
}

// grpcStatusName is the enum name of a gRPC status code. A code outside the
// enum is recorded as its digits rather than dropped: an unknown outcome is
// still an outcome, and a blank column would hide it.
func grpcStatusName(code int64) string {
	if code < 0 || code >= int64(len(grpcStatusNames)) {
		return strconv.FormatInt(code, 10)
	}
	return grpcStatusNames[code]
}

// What a caller-side span says about the thing it called. These are read only
// to name a callee that never answered for itself, so nothing here is stored as
// a column of `spans` — see serviceedges.Peer for what is done with them.
//
// Two of the three were respelled and real fleets emit both generations, so
// both are read. Unlike HTTP the envelope's semconv_version is not consulted:
// each generation has a key of its own, so the payload disambiguates itself.
const (
	// db.system became db.system.name in semconv 1.33.0. Measured against the
	// OpenTelemetry Demo, one image emits each: `cart` writes db.system=redis
	// while `accounting` writes db.system.name=postgresql.
	attrDBSystemCurrent = "db.system.name"
	attrDBSystemLegacy  = "db.system"

	// peer.service became service.peer.name. The conventions say either SHOULD
	// equal the remote's own `service.name`, which makes it the best thing a
	// caller can say about a peer that is not reporting.
	attrPeerServiceCurrent = "service.peer.name"
	attrPeerServiceLegacy  = "peer.service"

	// Never respelled. server.address is the host the caller dialled;
	// messaging.system is the broker technology it published to.
	attrServerAddress   = "server.address"
	attrMessagingSystem = "messaging.system"
)

// dbSystemRenames are the db.system values 1.33.0 respelled, from the spec's own
// migration table. Applying them here means one stored vocabulary, so a graph
// does not draw two nodes for one database because half the fleet has migrated.
//
// The values that table lists as removed are not here: they have no current
// spelling, and keeping them as they arrived says what the caller said.
// https://opentelemetry.io/docs/specs/semconv/non-normative/db-migration/
var dbSystemRenames = map[string]string{
	"adabas":             "softwareag.adabas",
	"intersystems_cache": "intersystems.cache",
	"cosmosdb":           "azure.cosmosdb",
	"db2":                "ibm.db2",
	"dynamodb":           "aws.dynamodb",
	"firebird":           "firebirdsql",
	"h2":                 "h2database",
	"hanadb":             "sap.hana",
	"informix":           "ibm.informix",
	"ingres":             "actian.ingres",
	"maxdb":              "sap.maxdb",
	"mssql":              "microsoft.sql_server",
	"netezza":            "ibm.netezza",
	"oracle":             "oracle.db",
	"redshift":           "aws.redshift",
	"spanner":            "gcp.spanner",
}

// canonicalDBSystem is the current spelling of a database system name. A value
// outside the table is kept as it arrived: either it never changed, or it is one
// the spec removed and we have nothing truer to call it.
func canonicalDBSystem(system string) string {
	if renamed, ok := dbSystemRenames[system]; ok {
		return renamed
	}
	return system
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
