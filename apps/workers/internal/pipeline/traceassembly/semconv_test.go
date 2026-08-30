package traceassembly

import (
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
)

// doubleValue is how a JSON-encoded exporter carries a number: it has no
// integer type, so a status code arrives as 14.0 from those SDKs.
func doubleValue(value float64) *commonpb.AnyValue {
	return &commonpb.AnyValue{Value: &commonpb.AnyValue_DoubleValue{DoubleValue: value}}
}

func TestParseSemconvVersion(t *testing.T) {
	cases := map[string]struct {
		raw       string
		want      semconvVersion
		wantValid bool
	}{
		"major minor patch":  {"1.27.0", semconvVersion{1, 27}, true},
		"major minor only":   {"1.27", semconvVersion{1, 27}, true},
		"v prefixed":         {"v1.23.1", semconvVersion{1, 23}, true},
		"surrounding space":  {" 1.23.0 ", semconvVersion{1, 23}, true},
		"empty":              {"", semconvVersion{}, false},
		"major only":         {"1", semconvVersion{}, false},
		"not a number":       {"latest", semconvVersion{}, false},
		"minor not a number": {"1.x.0", semconvVersion{}, false},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got, ok := parseSemconvVersion(tc.raw)
			if ok != tc.wantValid {
				t.Fatalf("parseSemconvVersion(%q) valid = %v, want %v", tc.raw, ok, tc.wantValid)
			}
			if ok && got != tc.want {
				t.Errorf("parseSemconvVersion(%q) = %+v, want %+v", tc.raw, got, tc.want)
			}
		})
	}
}

// The version chooses which spelling wins, never whether a spelling is looked
// at: both generations are always tried, so a mislabelled envelope still fills
// its promoted columns.
func TestHTTPNamesForAlwaysOffersBothGenerations(t *testing.T) {
	cases := map[string]struct {
		semconv string
		wantTop httpAttributeNames
	}{
		"stabilization release": {"1.23.0", stableHTTPNames},
		"after stabilization":   {"1.27.0", stableHTTPNames},
		"before stabilization":  {"1.22.0", legacyHTTPNames},
		"much older major":      {"0.9.0", legacyHTTPNames},
		"absent":                {"", stableHTTPNames},
		"unparseable":           {"unknown", stableHTTPNames},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got := httpNamesFor(tc.semconv)
			if got[0] != tc.wantTop {
				t.Errorf("httpNamesFor(%q) preferred %+v, want %+v", tc.semconv, got[0], tc.wantTop)
			}
			if got[1] == got[0] {
				t.Error("both entries are the same generation; the fallback is lost")
			}
		})
	}
}

// The gRPC call the OpenTelemetry Demo's product-catalog service makes, which
// is what the promoted columns have to end up holding whichever generation of
// the RPC conventions an SDK emits.
const (
	demoRPCService   = "oteldemo.ProductCatalogService"
	demoRPCMethod    = "ListProducts"
	demoRPCQualified = demoRPCService + "/" + demoRPCMethod
)

// Five of the demo's twelve services emit no HTTP attributes at all, and they
// split across both generations of the RPC conventions. Either way the three
// columns must come out identical, because a filter is written once.
func TestPromotedRPCColumnsAreTheSameAcrossConventions(t *testing.T) {
	cases := map[string][]*commonpb.KeyValue{
		"current spellings, 1.39.0 onwards": {
			attribute(attrRPCSystemCurrent, stringValue("grpc")),
			attribute(attrRPCMethod, stringValue(demoRPCQualified)),
			attribute(attrRPCStatusCurrent, stringValue("UNAVAILABLE")),
		},
		"legacy spellings, before 1.39.0": {
			attribute(attrRPCSystemLegacy, stringValue("grpc")),
			attribute(attrRPCServiceLegacy, stringValue(demoRPCService)),
			attribute(attrRPCMethod, stringValue(demoRPCMethod)),
			attribute(attrRPCStatusGRPCLegacy, intValue(14)),
		},
		"a fleet mid-migration, mixing both": {
			attribute(attrRPCSystemLegacy, stringValue("grpc")),
			attribute(attrRPCMethod, stringValue(demoRPCQualified)),
			attribute(attrRPCStatusGRPCLegacy, intValue(14)),
		},
		// A JSON-encoded exporter has no integer type, so the legacy code
		// arrives as a double from those SDKs.
		"legacy status as a double": {
			attribute(attrRPCSystemLegacy, stringValue("grpc")),
			attribute(attrRPCServiceLegacy, stringValue(demoRPCService)),
			attribute(attrRPCMethod, stringValue(demoRPCMethod)),
			attribute(attrRPCStatusGRPCLegacy, doubleValue(14)),
		},
	}

	for name, attributes := range cases {
		t.Run(name, func(t *testing.T) {
			row := flattenOne(t, marshalEnvelope(t, "", spanOptions{attributes: attributes}))

			if row.RPCSystem != "grpc" {
				t.Errorf("RPCSystem = %q, want %q", row.RPCSystem, "grpc")
			}
			if row.RPCMethod != demoRPCQualified {
				t.Errorf("RPCMethod = %q, want %q", row.RPCMethod, demoRPCQualified)
			}
			if row.RPCStatus != "UNAVAILABLE" {
				t.Errorf("RPCStatus = %q, want %q", row.RPCStatus, "UNAVAILABLE")
			}
		})
	}
}

// `rpc/dup` mode emits both generations, and they collide on `rpc.method`:
// one key, holding the bare name for one generation and the qualified name for
// the other. Qualifying an already-qualified name would double the service.
func TestDupModeDoesNotQualifyTheMethodTwice(t *testing.T) {
	row := flattenOne(t, marshalEnvelope(t, "", spanOptions{attributes: []*commonpb.KeyValue{
		attribute(attrRPCServiceLegacy, stringValue(demoRPCService)),
		attribute(attrRPCMethod, stringValue(demoRPCQualified)),
	}}))

	if row.RPCMethod != demoRPCQualified {
		t.Errorf("RPCMethod = %q, want %q", row.RPCMethod, demoRPCQualified)
	}
}

// The status column holds one vocabulary whatever framework produced the span,
// which is what 1.39.0 collapsed the per-framework codes into.
func TestEveryFrameworksLegacyStatusReachesTheOneColumn(t *testing.T) {
	cases := map[string]struct {
		attribute *commonpb.KeyValue
		want      string
	}{
		"gRPC integer becomes its enum name": {
			attribute(attrRPCStatusGRPCLegacy, intValue(4)), "DEADLINE_EXCEEDED",
		},
		"Connect RPC code is already a string": {
			attribute(attrRPCStatusConnectLegacy, stringValue("unavailable")), "unavailable",
		},
		"JSON-RPC code is its own number": {
			attribute(attrRPCStatusJSONRPCLegacy, intValue(-32602)), "-32602",
		},
		"the current spelling is taken as it stands": {
			attribute(attrRPCStatusCurrent, stringValue("NOT_FOUND")), "NOT_FOUND",
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			row := flattenOne(t, marshalEnvelope(t, "", spanOptions{
				attributes: []*commonpb.KeyValue{tc.attribute},
			}))
			if row.RPCStatus != tc.want {
				t.Errorf("RPCStatus = %q, want %q", row.RPCStatus, tc.want)
			}
		})
	}
}

func TestGRPCStatusName(t *testing.T) {
	cases := map[int64]string{
		0:  "OK",
		4:  "DEADLINE_EXCEEDED",
		14: "UNAVAILABLE",
		16: "UNAUTHENTICATED",
		// Outside the enum: recorded rather than dropped, so an outcome we do
		// not recognise is still visible.
		17: "17",
		-1: "-1",
	}
	for code, want := range cases {
		if got := grpcStatusName(code); got != want {
			t.Errorf("grpcStatusName(%d) = %q, want %q", code, got, want)
		}
	}
}

// 1.39.0 respelled two of the system values as well as the attribute key.
func TestRPCSystemValuesAreNormalized(t *testing.T) {
	cases := map[string]string{
		"apache_dubbo": "dubbo",
		"connect_rpc":  "connectrpc",
		"grpc":         "grpc",
		"jsonrpc":      "jsonrpc",
		// Removed from the enum but still legal as a custom value.
		"java_rmi": "java_rmi",
		"":         "",
	}
	for system, want := range cases {
		if got := canonicalRPCSystem(system); got != want {
			t.Errorf("canonicalRPCSystem(%q) = %q, want %q", system, got, want)
		}
	}
}

func TestQualifiedRPCMethod(t *testing.T) {
	cases := map[string]struct {
		service, method, want string
	}{
		"legacy pair is joined":        {demoRPCService, demoRPCMethod, demoRPCQualified},
		"already qualified is kept":    {demoRPCService, demoRPCQualified, demoRPCQualified},
		"no service leaves it alone":   {"", demoRPCQualified, demoRPCQualified},
		"no method stays empty":        {demoRPCService, "", ""},
		"neither stays empty":          {"", "", ""},
		"unrecognised method sentinel": {demoRPCService, "_OTHER", demoRPCService + "/_OTHER"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := qualifiedRPCMethod(tc.service, tc.method); got != tc.want {
				t.Errorf("qualifiedRPCMethod(%q, %q) = %q, want %q",
					tc.service, tc.method, got, tc.want)
			}
		})
	}
}

// An HTTP span must not acquire RPC values, and a gRPC span must not acquire
// HTTP ones: the two sets of columns are separate precisely so neither code
// space contaminates the other.
func TestTheTwoSetsOfColumnsStayApart(t *testing.T) {
	httpSpan := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		attributes: []*commonpb.KeyValue{
			attribute(attrHTTPMethodStable, stringValue("GET")),
			attribute(attrHTTPRoute, stringValue("/orders/:id")),
			attribute(attrHTTPStatusStable, intValue(200)),
		},
	}))
	if httpSpan.RPCSystem != "" || httpSpan.RPCMethod != "" || httpSpan.RPCStatus != "" {
		t.Errorf("an HTTP span filled the RPC columns: %q %q %q",
			httpSpan.RPCSystem, httpSpan.RPCMethod, httpSpan.RPCStatus)
	}

	grpcSpan := flattenOne(t, marshalEnvelope(t, "1.27.0", spanOptions{
		attributes: []*commonpb.KeyValue{
			attribute(attrRPCSystemCurrent, stringValue("grpc")),
			attribute(attrRPCMethod, stringValue(demoRPCQualified)),
			attribute(attrRPCStatusCurrent, stringValue("OK")),
		},
	}))
	if grpcSpan.HTTPMethod != "" || grpcSpan.HTTPRoute != "" || grpcSpan.HTTPStatus != 0 {
		t.Errorf("a gRPC span filled the HTTP columns: %q %q %d",
			grpcSpan.HTTPMethod, grpcSpan.HTTPRoute, grpcSpan.HTTPStatus)
	}
}
