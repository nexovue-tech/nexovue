package serviceedges

import "testing"

// A call nothing answered used to point at one reserved node whatever it had
// called. Measured against the OpenTelemetry Demo, that node held cart→Redis,
// product-catalog→Postgres, load-generator→frontend and three more at once, and
// drew them as a hub the customer does not have. These tests are the naming
// rules that broke it apart, and every one of them is a value the *caller* put
// on its own span — nothing here is inferred.

func clientWithPeer(peer Peer) Span {
	span := testSpan(KindClient, "cart", 0x01, 0)
	span.Peer = peer
	return span
}

func TestAnUnansweredCallIsNamedByWhatTheCallerSaidItCalled(t *testing.T) {
	for name, expectation := range map[string]struct {
		peer   Peer
		callee string
		kind   string
	}{
		"a declared peer name beats everything else": {
			peer:   Peer{Service: "AuthTokenCache", Address: "10.0.0.4", System: "redis"},
			callee: "(AuthTokenCache)",
			kind:   "redis",
		},
		"the host it dialled beats the technology": {
			peer:   Peer{Address: "valkey-cart", System: "redis"},
			callee: "(valkey-cart)",
			kind:   "redis",
		},
		"the datastore names it when no host was recorded": {
			peer:   Peer{System: "postgresql"},
			callee: "(postgresql)",
			kind:   "postgresql",
		},
		"a broker is a dependency in its own right": {
			peer:   Peer{System: "kafka"},
			callee: "(kafka)",
			kind:   "kafka",
		},
		// The one that would rebuild the hub. `grpc` is how the call travelled,
		// not what answered it: every unanswered gRPC call in the fleet would
		// share one `(grpc)` node.
		"a transport never names a node": {
			peer:   Peer{Transport: "grpc"},
			callee: UninstrumentedService,
			kind:   "grpc",
		},
		"a transport still labels an edge named by its host": {
			peer:   Peer{Address: "flagd", Transport: "grpc"},
			callee: "(flagd)",
			kind:   "grpc",
		},
		"a span that said nothing still reports its call": {
			peer:   Peer{},
			callee: UninstrumentedService,
			kind:   "",
		},
	} {
		t.Run(name, func(t *testing.T) {
			accumulator := newTestAccumulator(t)
			edges := drainEdges(t, accumulator, clientWithPeer(expectation.peer))

			if len(edges) != 1 {
				t.Fatalf("expected the call to be reported once, got %+v", edges)
			}
			if edges[0].ServerService != expectation.callee {
				t.Fatalf("expected callee %q, got %q", expectation.callee, edges[0].ServerService)
			}
			if edges[0].ServerPeerKind != expectation.kind {
				t.Fatalf("expected kind %q, got %q", expectation.kind, edges[0].ServerPeerKind)
			}
		})
	}
}

// The whole point: two callers reaching two different peers are two nodes.
func TestTwoUnansweredCallsToDifferentPeersAreTwoNodes(t *testing.T) {
	accumulator := newTestAccumulator(t)
	redis := clientWithPeer(Peer{Address: "valkey-cart", System: "redis"})
	postgres := testSpan(KindClient, "product-catalog", 0x02, 0)
	postgres.Peer = Peer{Address: "astronomy-db", System: "postgresql"}

	edges := drainEdges(t, accumulator, redis, postgres)

	callees := make(map[string]string, len(edges))
	for _, edge := range edges {
		callees[edge.ServerService] = edge.ServerPeerKind
	}
	if len(callees) != 2 {
		t.Fatalf("expected two distinct callees, got %+v", callees)
	}
	if callees["(valkey-cart)"] != "redis" || callees["(astronomy-db)"] != "postgresql" {
		t.Fatalf("expected the two peers named and labelled, got %+v", callees)
	}
}

// A callee that reported for itself names itself, and carries no label: what
// the caller believed about it is not evidence about the callee.
func TestAPairedCalleeIgnoresWhatTheCallerBelievedAboutIt(t *testing.T) {
	accumulator := newTestAccumulator(t)
	client := clientWithPeer(Peer{Address: "10.0.0.4", System: "redis"})
	server := testSpan(KindServer, "checkout", 0x02, 0x01)

	edges := drainEdges(t, accumulator, client, server)

	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %+v", edges)
	}
	if edges[0].ServerService != "checkout" || edges[0].ServerPeerKind != "" {
		t.Fatalf("expected an unlabelled edge to checkout, got %q/%q",
			edges[0].ServerService, edges[0].ServerPeerKind)
	}
}

// The kind is part of the grouping key, so the block a drain produces is the
// same block every time — which is what the store's insert deduplication
// recognises a retry by.
func TestEdgesDrainInAStableOrderWhenOnlyTheKindDiffers(t *testing.T) {
	spans := []Span{
		clientWithPeer(Peer{Address: "flagd", Transport: "grpc"}),
		clientWithPeer(Peer{Address: "flagd"}),
	}
	spans[1].SpanID = []byte{0x02}

	first := drainEdges(t, newTestAccumulator(t), spans...)
	second := drainEdges(t, newTestAccumulator(t), spans[1], spans[0])

	if len(first) != 2 || len(second) != 2 {
		t.Fatalf("expected two edges each way, got %d and %d", len(first), len(second))
	}
	for i := range first {
		if first[i].ServerPeerKind != second[i].ServerPeerKind {
			t.Fatalf("arrival order changed the block: %+v vs %+v", first, second)
		}
	}
}
