// Package tracesplit divides one OTLP trace export into one export per trace id.
//
// Trace assembly downstream depends on Kafka partitioning by trace id, so every
// span of a trace must reach the same worker. One ExportTraceServiceRequest can
// carry spans from many traces, so the receiver splits before it publishes —
// the same thing the OpenTelemetry collector's loadbalancing exporter does.
//
// Each span keeps the resource and scope it arrived with. That association is
// the whole correctness question here: a span that loses its resource loses the
// service it belongs to.
package tracesplit

import (
	"encoding/hex"
	"errors"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/semconv"
)

// traceIDLength is the width OTLP fixes for a trace id.
const traceIDLength = 16

// ErrInvalidTraceID means a span carried something that is not a trace id. The
// payload is malformed, which the transport reports as a non-retryable failure
// rather than dropping the span quietly.
var ErrInvalidTraceID = errors.New("span carries a trace id that is not 16 bytes")

// Group is one trace's spans, re-encodable as a request of their own.
type Group struct {
	// TraceID is lower-case hex, which is how the trace id is written
	// everywhere else in the system and what becomes the Kafka key.
	TraceID string

	// SemconvVersion is the OTel convention generation the spans declare, taken
	// from the schema URL they arrived with, or empty when they declared none.
	SemconvVersion string

	Request *collectorpb.ExportTraceServiceRequest
}

// ByTraceID groups the spans of req by trace id, preserving each span's own
// resource and scope. Groups come back in the order their trace was first seen,
// so the same input always produces the same output.
//
// The returned requests share the resource, scope and span messages of req
// rather than copying them. Nothing mutates those, and the copy would be the
// expensive half of this operation.
func ByTraceID(req *collectorpb.ExportTraceServiceRequest) ([]Group, error) {
	split := newSplit()

	for _, resourceSpans := range req.GetResourceSpans() {
		for _, scopeSpans := range resourceSpans.GetScopeSpans() {
			for _, span := range scopeSpans.GetSpans() {
				if len(span.GetTraceId()) != traceIDLength {
					return nil, ErrInvalidTraceID
				}
				traceID := hex.EncodeToString(span.GetTraceId())
				split.add(traceID, resourceSpans, scopeSpans, span)
			}
		}
	}
	return split.groups(), nil
}

// split accumulates one destination request per trace id.
type split struct {
	order []string
	byID  map[string]*traceGroup
}

func newSplit() *split {
	return &split{byID: make(map[string]*traceGroup)}
}

func (s *split) add(
	traceID string,
	resourceSpans *tracepb.ResourceSpans,
	scopeSpans *tracepb.ScopeSpans,
	span *tracepb.Span,
) {
	group, ok := s.byID[traceID]
	if !ok {
		group = newTraceGroup(traceID)
		s.byID[traceID] = group
		s.order = append(s.order, traceID)
	}
	group.add(resourceSpans, scopeSpans, span)
}

func (s *split) groups() []Group {
	groups := make([]Group, 0, len(s.order))
	for _, traceID := range s.order {
		groups = append(groups, s.byID[traceID].result())
	}
	return groups
}

// traceGroup builds one trace's request. The two maps are keyed by the pointers
// from the incoming request: that is what keeps a span attached to the exact
// resource and scope it arrived under, even when two resources look identical.
type traceGroup struct {
	traceID        string
	semconvVersion string
	request        *collectorpb.ExportTraceServiceRequest
	resources      map[*tracepb.ResourceSpans]*tracepb.ResourceSpans
	scopes         map[*tracepb.ScopeSpans]*tracepb.ScopeSpans
}

func newTraceGroup(traceID string) *traceGroup {
	return &traceGroup{
		traceID:   traceID,
		request:   &collectorpb.ExportTraceServiceRequest{},
		resources: make(map[*tracepb.ResourceSpans]*tracepb.ResourceSpans),
		scopes:    make(map[*tracepb.ScopeSpans]*tracepb.ScopeSpans),
	}
}

func (g *traceGroup) add(
	resourceSpans *tracepb.ResourceSpans,
	scopeSpans *tracepb.ScopeSpans,
	span *tracepb.Span,
) {
	destination := g.scopeFor(g.resourceFor(resourceSpans), scopeSpans)
	destination.Spans = append(destination.Spans, span)

	if g.semconvVersion == "" {
		g.semconvVersion = semconv.FromSchemaURLs(
			scopeSpans.GetSchemaUrl(), resourceSpans.GetSchemaUrl())
	}
}

func (g *traceGroup) resourceFor(source *tracepb.ResourceSpans) *tracepb.ResourceSpans {
	if existing, ok := g.resources[source]; ok {
		return existing
	}
	// The Resource message itself is shared, not copied: only the span lists
	// differ between the split requests.
	destination := &tracepb.ResourceSpans{
		Resource:  source.GetResource(),
		SchemaUrl: source.GetSchemaUrl(),
	}
	g.resources[source] = destination
	g.request.ResourceSpans = append(g.request.ResourceSpans, destination)
	return destination
}

func (g *traceGroup) scopeFor(
	parent *tracepb.ResourceSpans,
	source *tracepb.ScopeSpans,
) *tracepb.ScopeSpans {
	if existing, ok := g.scopes[source]; ok {
		return existing
	}
	destination := &tracepb.ScopeSpans{
		Scope:     source.GetScope(),
		SchemaUrl: source.GetSchemaUrl(),
	}
	g.scopes[source] = destination
	parent.ScopeSpans = append(parent.ScopeSpans, destination)
	return destination
}

func (g *traceGroup) result() Group {
	return Group{
		TraceID:        g.traceID,
		SemconvVersion: g.semconvVersion,
		Request:        g.request,
	}
}
