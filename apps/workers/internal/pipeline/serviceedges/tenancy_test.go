package serviceedges

import "testing"

// Trace and span ids are not unique across tenants. W3C trace context is
// designed to propagate between organisations, so one customer calling
// another's API forwards its traceparent and both export the same ids. A
// pairing key without tenancy would join one tenant's caller to another's
// callee and draw an edge between services that never spoke — and it would leak
// the callee's service name into the wrong tenant's graph.
func TestSpansDoNotPairAcrossTenants(t *testing.T) {
	accumulator := newTestAccumulator(t)

	client := testSpan(KindClient, "checkout", 0x01, 0)
	server := testSpan(KindServer, "payments", 0x02, 0x01)
	server.TenantID = "tenant-b"

	edges := drainEdges(t, accumulator, client, server)

	if len(edges) != 1 {
		t.Fatalf("expected only tenant-a's unpaired call, got %+v", edges)
	}
	if edges[0].TenantID != "tenant-a" {
		t.Fatalf("expected the edge to belong to tenant-a, got %q", edges[0].TenantID)
	}
	if edges[0].ServerService != UninstrumentedService {
		t.Fatalf("tenant-b's span was paired across the boundary: callee is %q", edges[0].ServerService)
	}
	if got := accumulator.Stats().Paired; got != 0 {
		t.Fatalf("expected no pairing, got %d", got)
	}
}

// Projects are isolated for the same reason: one tenant's two projects can
// legitimately be two unrelated systems.
func TestSpansDoNotPairAcrossProjects(t *testing.T) {
	accumulator := newTestAccumulator(t)

	client := testSpan(KindClient, "checkout", 0x01, 0)
	server := testSpan(KindServer, "payments", 0x02, 0x01)
	server.ProjectID = "project-b"

	edges := drainEdges(t, accumulator, client, server)

	if len(edges) != 1 || edges[0].ServerService != UninstrumentedService {
		t.Fatalf("spans paired across projects: %+v", edges)
	}
}

// Span ids are only unique within a trace. Two traces of one tenant reusing an
// id — a broken SDK, a zeroed field — must not cross-pair either.
func TestSpansDoNotPairAcrossTraces(t *testing.T) {
	accumulator := newTestAccumulator(t)

	client := testSpan(KindClient, "checkout", 0x01, 0)
	server := testSpan(KindServer, "payments", 0x02, 0x01)
	server.TraceID = []byte{0xbb}

	edges := drainEdges(t, accumulator, client, server)

	if len(edges) != 1 || edges[0].ServerService != UninstrumentedService {
		t.Fatalf("spans paired across traces: %+v", edges)
	}
}

// Two tenants running the same ids at the same time each keep their own edge.
func TestEachTenantKeepsItsOwnEdge(t *testing.T) {
	accumulator := newTestAccumulator(t)

	spans := []Span{
		testSpan(KindClient, "checkout", 0x01, 0),
		testSpan(KindServer, "payments", 0x02, 0x01),
	}
	for _, span := range spans {
		other := span
		other.TenantID = "tenant-b"
		other.ServiceName = "b-" + span.ServiceName
		accumulator.Observe(baseTime, span)
		accumulator.Observe(baseTime, other)
	}

	edges := accumulator.Drain(settledTime)
	if len(edges) != 2 {
		t.Fatalf("expected one edge per tenant, got %+v", edges)
	}
	for _, edge := range edges {
		if edge.ServerService == UninstrumentedService {
			t.Fatalf("a tenant's own pair failed to form: %+v", edge)
		}
	}
	if edges[0].TenantID == edges[1].TenantID {
		t.Fatalf("both edges landed on %q", edges[0].TenantID)
	}
}
