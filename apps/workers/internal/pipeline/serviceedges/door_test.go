package serviceedges

import "testing"

// A site key is printed in its page, so anyone can send spans through the
// browser door under any service name. The edge rollup still takes them — the
// map draws a browser's calls — but every row says which door it came through,
// so F3's rules can read the server door alone (F3 A63). These tests hold that
// a browser span can never land on a server-door row.

func TestAnEdgeSaysWhichDoorItsCallerCameThrough(t *testing.T) {
	for name, fromBrowser := range map[string]bool{"server door": false, "browser door": true} {
		t.Run(name, func(t *testing.T) {
			client := testSpan(KindClient, "web", 0x01, 0)
			client.FromBrowserDoor = fromBrowser
			edges := drainEdges(t, newTestAccumulator(t), client)

			want := DoorServer
			if fromBrowser {
				want = DoorBrowser
			}
			if len(edges) != 1 || edges[0].Door != want {
				t.Fatalf("expected one %q edge, got %+v", want, edges)
			}
		})
	}
}

// A forged SERVER span answering a real call would otherwise make the edge
// look counted from the server door.
func TestABrowserCalleeMakesItsEdgeABrowserEdge(t *testing.T) {
	client := testSpan(KindClient, "checkout", 0x01, 0)
	server := testSpan(KindServer, "payments", 0x02, 0x01)
	server.FromBrowserDoor = true

	edges := drainEdges(t, newTestAccumulator(t), client, server)

	if len(edges) != 1 || edges[0].Door != DoorBrowser {
		t.Fatalf("expected one browser edge, got %+v", edges)
	}
}

// The door is part of the row's identity: a browser's forged call and a real
// one in the same minute stay two rows, or a merge would pick one door for both.
func TestTheTwoDoorsNeverShareARow(t *testing.T) {
	real := testSpan(KindClient, "checkout", 0x01, 0)
	forged := testSpan(KindClient, "checkout", 0x02, 0)
	forged.FromBrowserDoor = true

	edges := drainEdges(t, newTestAccumulator(t), real, forged)

	if len(edges) != 2 {
		t.Fatalf("expected one row per door, got %+v", edges)
	}
	doors := map[string]uint64{}
	for _, edge := range edges {
		doors[edge.Door] += edge.CallCount
	}
	if doors[DoorServer] != 1 || doors[DoorBrowser] != 1 {
		t.Fatalf("expected one call through each door, got %v", doors)
	}
}

// A told-only callee keeps the attribute its name came from, so a link can
// filter on that same attribute (F3 A76). A callee that reported for itself, or
// one named only by its technology, has none.
func TestAnUnansweredCallKeepsTheAttributeThatNamedItsCallee(t *testing.T) {
	for name, expectation := range map[string]struct {
		peer Peer
		attr string
	}{
		"the current peer name": {
			peer: Peer{Service: "payments", ServiceAttr: AttrServicePeerName, Address: "10.0.0.4"},
			attr: AttrServicePeerName,
		},
		"the legacy peer name": {
			peer: Peer{Service: "payments", ServiceAttr: AttrPeerService},
			attr: AttrPeerService,
		},
		"the host it dialled": {
			peer: Peer{Address: "valkey-cart", System: "redis"},
			attr: AttrServerAddress,
		},
		"a technology is not an attribute a link can match": {
			peer: Peer{System: "postgresql"},
			attr: "",
		},
		"a span that said nothing": {peer: Peer{}, attr: ""},
	} {
		t.Run(name, func(t *testing.T) {
			edges := drainEdges(t, newTestAccumulator(t), clientWithPeer(expectation.peer))

			if len(edges) != 1 || edges[0].ServerPeerAttr != expectation.attr {
				t.Fatalf("expected attribute %q, got %+v", expectation.attr, edges)
			}
		})
	}
}

func TestACalleeThatReportedForItselfHasNoPeerAttribute(t *testing.T) {
	client := testSpan(KindClient, "checkout", 0x01, 0)
	client.Peer = Peer{Service: "payments", ServiceAttr: AttrPeerService}
	server := testSpan(KindServer, "payments", 0x02, 0x01)

	edges := drainEdges(t, newTestAccumulator(t), client, server)

	if len(edges) != 1 || edges[0].ServerPeerAttr != "" {
		t.Fatalf("expected no peer attribute on a paired edge, got %+v", edges)
	}
}
