package tracesplit_test

import (
	"encoding/hex"
	"testing"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/tracesplit"
)

const schemaURL = "https://opentelemetry.io/schemas/1.43.0"

// traceID builds a distinguishable 16-byte trace id from a single marker byte.
func traceID(marker byte) []byte {
	id := make([]byte, 16)
	for i := range id {
		id[i] = marker
	}
	return id
}

func span(name string, trace []byte) *tracepb.Span {
	return &tracepb.Span{Name: name, TraceId: trace, SpanId: make([]byte, 8)}
}

func resource(service string) *resourcepb.Resource {
	return &resourcepb.Resource{Attributes: []*commonpb.KeyValue{{
		Key:   "service.name",
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: service}},
	}}}
}

func serviceNameOf(resourceSpans *tracepb.ResourceSpans) string {
	for _, attribute := range resourceSpans.GetResource().GetAttributes() {
		if attribute.GetKey() == "service.name" {
			return attribute.GetValue().GetStringValue()
		}
	}
	return ""
}

// spanNames flattens a split request so a test can assert what landed in it.
func spanNames(request *collectorpb.ExportTraceServiceRequest) []string {
	var names []string
	for _, resourceSpans := range request.GetResourceSpans() {
		for _, scopeSpans := range resourceSpans.GetScopeSpans() {
			for _, s := range scopeSpans.GetSpans() {
				names = append(names, s.GetName())
			}
		}
	}
	return names
}

func TestByTraceIDGroupsSpansByTrace(t *testing.T) {
	first, second := traceID(0x11), traceID(0x22)
	request := &collectorpb.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{{
			Resource: resource("checkout"),
			ScopeSpans: []*tracepb.ScopeSpans{{
				Spans: []*tracepb.Span{
					span("a", first),
					span("b", second),
					span("c", first),
				},
			}},
		}},
	}

	groups, err := tracesplit.ByTraceID(request)
	if err != nil {
		t.Fatalf("ByTraceID() = %v, want no error", err)
	}
	if len(groups) != 2 {
		t.Fatalf("got %d groups, want one per trace id", len(groups))
	}

	// Order is first-seen, so the split is reproducible.
	if groups[0].TraceID != hex.EncodeToString(first) {
		t.Errorf("groups[0].TraceID = %q, want the first trace seen", groups[0].TraceID)
	}
	if got := spanNames(groups[0].Request); len(got) != 2 || got[0] != "a" || got[1] != "c" {
		t.Errorf("first trace holds %v, want [a c]", got)
	}
	if got := spanNames(groups[1].Request); len(got) != 1 || got[0] != "b" {
		t.Errorf("second trace holds %v, want [b]", got)
	}
}

func TestByTraceIDKeepsEachSpanWithItsOwnResource(t *testing.T) {
	shared := traceID(0x33)
	request := &collectorpb.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{
			{
				Resource:   resource("checkout"),
				SchemaUrl:  schemaURL,
				ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span("checkout-span", shared)}}},
			},
			{
				Resource:   resource("payments"),
				SchemaUrl:  schemaURL,
				ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span("payments-span", shared)}}},
			},
		},
	}

	groups, err := tracesplit.ByTraceID(request)
	if err != nil {
		t.Fatalf("ByTraceID() = %v, want no error", err)
	}
	if len(groups) != 1 {
		t.Fatalf("got %d groups, want one: every span shares a trace id", len(groups))
	}

	resourceSpans := groups[0].Request.GetResourceSpans()
	if len(resourceSpans) != 2 {
		t.Fatalf("got %d resources, want both preserved", len(resourceSpans))
	}
	for _, want := range []struct{ service, span string }{
		{"checkout", "checkout-span"},
		{"payments", "payments-span"},
	} {
		found := false
		for _, actual := range resourceSpans {
			if serviceNameOf(actual) != want.service {
				continue
			}
			found = true
			if got := spanNames(&collectorpb.ExportTraceServiceRequest{
				ResourceSpans: []*tracepb.ResourceSpans{actual},
			}); len(got) != 1 || got[0] != want.span {
				t.Errorf("%s carries %v, want [%s]", want.service, got, want.span)
			}
		}
		if !found {
			t.Errorf("resource %q lost its spans in the split", want.service)
		}
	}
}

func TestByTraceIDKeepsEachSpanWithItsOwnScope(t *testing.T) {
	shared := traceID(0x44)
	request := &collectorpb.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{{
			Resource: resource("checkout"),
			ScopeSpans: []*tracepb.ScopeSpans{
				{
					Scope: &commonpb.InstrumentationScope{Name: "net/http"},
					Spans: []*tracepb.Span{span("http-span", shared)},
				},
				{
					Scope: &commonpb.InstrumentationScope{Name: "database/sql"},
					Spans: []*tracepb.Span{span("sql-span", shared)},
				},
			},
		}},
	}

	groups, err := tracesplit.ByTraceID(request)
	if err != nil {
		t.Fatalf("ByTraceID() = %v, want no error", err)
	}

	scopes := groups[0].Request.GetResourceSpans()[0].GetScopeSpans()
	if len(scopes) != 2 {
		t.Fatalf("got %d scopes, want both preserved", len(scopes))
	}
	byScope := map[string]string{}
	for _, scopeSpans := range scopes {
		byScope[scopeSpans.GetScope().GetName()] = scopeSpans.GetSpans()[0].GetName()
	}
	if byScope["net/http"] != "http-span" || byScope["database/sql"] != "sql-span" {
		t.Errorf("scopes carry %v, want each span under the scope it arrived with", byScope)
	}
}

// Two resources can be byte-identical and still be separate entries. Grouping by
// value rather than by the message that arrived would silently merge them.
func TestByTraceIDDoesNotMergeIdenticalResources(t *testing.T) {
	shared := traceID(0x55)
	request := &collectorpb.ExportTraceServiceRequest{
		ResourceSpans: []*tracepb.ResourceSpans{
			{Resource: resource("checkout"), ScopeSpans: []*tracepb.ScopeSpans{
				{Spans: []*tracepb.Span{span("one", shared)}},
			}},
			{Resource: resource("checkout"), ScopeSpans: []*tracepb.ScopeSpans{
				{Spans: []*tracepb.Span{span("two", shared)}},
			}},
		},
	}

	groups, err := tracesplit.ByTraceID(request)
	if err != nil {
		t.Fatalf("ByTraceID() = %v, want no error", err)
	}
	if got := len(groups[0].Request.GetResourceSpans()); got != 2 {
		t.Errorf("got %d resources, want 2: identical resources are still distinct entries", got)
	}
	if got := spanNames(groups[0].Request); len(got) != 2 {
		t.Errorf("got spans %v, want both preserved", got)
	}
}

func TestByTraceIDReadsSemconvVersionFromTheSchemaURL(t *testing.T) {
	cases := map[string]struct {
		resourceSchema string
		scopeSchema    string
		want           string
	}{
		"scope wins over resource":        {schemaURL, "https://opentelemetry.io/schemas/1.44.0", "1.44.0"},
		"resource when scope is absent":   {schemaURL, "", "1.43.0"},
		"empty when neither declares one": {"", "", ""},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			request := &collectorpb.ExportTraceServiceRequest{
				ResourceSpans: []*tracepb.ResourceSpans{{
					SchemaUrl: tc.resourceSchema,
					ScopeSpans: []*tracepb.ScopeSpans{{
						SchemaUrl: tc.scopeSchema,
						Spans:     []*tracepb.Span{span("a", traceID(0x66))},
					}},
				}},
			}

			groups, err := tracesplit.ByTraceID(request)
			if err != nil {
				t.Fatalf("ByTraceID() = %v, want no error", err)
			}
			if groups[0].SemconvVersion != tc.want {
				t.Errorf("SemconvVersion = %q, want %q", groups[0].SemconvVersion, tc.want)
			}
		})
	}
}

func TestByTraceIDAcceptsBatchesWithNoSpans(t *testing.T) {
	cases := map[string]*collectorpb.ExportTraceServiceRequest{
		"empty request":       {},
		"resource, no scopes": {ResourceSpans: []*tracepb.ResourceSpans{{Resource: resource("checkout")}}},
		"scope, no spans": {ResourceSpans: []*tracepb.ResourceSpans{{
			ScopeSpans: []*tracepb.ScopeSpans{{Scope: &commonpb.InstrumentationScope{Name: "net/http"}}},
		}}},
	}
	for name, request := range cases {
		t.Run(name, func(t *testing.T) {
			groups, err := tracesplit.ByTraceID(request)
			if err != nil {
				t.Fatalf("ByTraceID() = %v, want no error", err)
			}
			if len(groups) != 0 {
				t.Errorf("got %d groups, want none: there is nothing to publish", len(groups))
			}
		})
	}
}

func TestByTraceIDRejectsAnUnusableTraceID(t *testing.T) {
	cases := map[string][]byte{
		"empty":     {},
		"too short": make([]byte, 8),
		"too long":  make([]byte, 32),
	}
	for name, id := range cases {
		t.Run(name, func(t *testing.T) {
			request := &collectorpb.ExportTraceServiceRequest{
				ResourceSpans: []*tracepb.ResourceSpans{{
					ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{span("a", id)}}},
				}},
			}

			if _, err := tracesplit.ByTraceID(request); err == nil {
				t.Fatal("ByTraceID() accepted a span that cannot be routed to a partition")
			}
		})
	}
}
