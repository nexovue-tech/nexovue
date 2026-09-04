package serviceedges

import "testing"

// edgesKept is the partition this worker goes on owning through the rebalance;
// edgesMoved is the one the group hands to another worker.
const (
	edgesKept  int32 = 4
	edgesMoved int32 = 9
)

// The partitions an edge was derived on are provenance, not a dimension: they
// are kept apart in memory so a handover can drop one, and summed back together
// before anything is written. A row per partition would multiply every count in
// `service_edges_1m` by the topic's partition count.
func TestPartitionSharesOfOneEdgeDrainAsOneRow(t *testing.T) {
	accumulator := newTestAccumulator(t)

	observeCall(accumulator, edgesKept, 0x01, 0x02)
	observeCall(accumulator, edgesMoved, 0x03, 0x04)

	edges := accumulator.Drain(settledTime)
	if len(edges) != 1 {
		t.Fatalf("drained %d edges, want one row for the one dependency: %+v", len(edges), edges)
	}
	if edges[0].CallCount != 2 {
		t.Errorf("call_count = %d, want 2", edges[0].CallCount)
	}
	if got := len(edges[0].LatencyMS); got != 2 {
		t.Errorf("latency sample held %d durations, want 2: the shares' samples "+
			"merge rather than one of them winning", got)
	}
}

// The fix itself, at the value that reaches ClickHouse. A partition the group
// took back has derived nothing here, because the worker that took it over
// derives the same edge from the same uncommitted records.
func TestARevokedPartitionsShareLeavesTheEdges(t *testing.T) {
	accumulator := newTestAccumulator(t)

	observeCall(accumulator, edgesKept, 0x01, 0x02)
	observeCall(accumulator, edgesMoved, 0x03, 0x04)

	dropped := accumulator.Disown([]int32{edgesMoved})
	if dropped.Edges != 1 || dropped.Calls != 1 {
		t.Errorf("Disown reported %+v, want one edge share of one call", dropped)
	}

	edges := accumulator.Drain(settledTime)
	if len(edges) != 1 || edges[0].CallCount != 1 {
		t.Fatalf("drained %+v, want one edge counting only the partition still owned", edges)
	}
}

// A caller still waiting for its partner is dropped, not settled. Settling it
// would record an unanswered edge — precisely the row the worker taking the
// partition over is about to derive for itself, and one that names
// `(uninstrumented)` when the real callee is about to be found.
func TestARevokedPartitionsParkedCallerRecordsNothing(t *testing.T) {
	accumulator := newTestAccumulator(t)

	caller := testSpan(KindClient, "checkout", 0x01, 0)
	caller.Partition = edgesMoved
	accumulator.Observe(baseTime, caller)

	dropped := accumulator.Disown([]int32{edgesMoved})
	if dropped.Pending != 1 {
		t.Errorf("Disown took %d spans out of the pairing buffer, want 1", dropped.Pending)
	}
	if held := accumulator.Stats().PendingSpans; held != 0 {
		t.Errorf("the pairing buffer still holds %d spans of the partition that left", held)
	}
	if edges := accumulator.DrainAll(); len(edges) != 0 {
		t.Errorf("drained %+v, want nothing: a dropped caller must not settle", edges)
	}
}

// A revoke names some partitions, not all of them. What it does not name goes
// on pairing and counting exactly as before.
func TestDisowningLeavesTheEdgesItDidNotName(t *testing.T) {
	accumulator := newTestAccumulator(t)

	observeCall(accumulator, edgesKept, 0x01, 0x02)
	if dropped := accumulator.Disown([]int32{edgesMoved}); dropped != (Disowned{}) {
		t.Errorf("Disown reported %+v, want nothing dropped", dropped)
	}
	if dropped := accumulator.Disown(nil); dropped != (Disowned{}) {
		t.Errorf("Disown(nil) reported %+v, want nothing dropped", dropped)
	}

	edges := accumulator.Drain(settledTime)
	if len(edges) != 1 || edges[0].CallCount != 1 {
		t.Fatalf("drained %+v, want the kept partition's one call", edges)
	}
}

// observeCall runs one CLIENT span and the SERVER span that answered it through
// the accumulator, both as records of one partition — which is what a call is:
// two spans of one trace, and so of one Kafka partition.
func observeCall(accumulator *Accumulator, partition int32, callerID, calleeID byte) {
	caller := testSpan(KindClient, "checkout", callerID, 0)
	caller.Partition = partition
	callee := testSpan(KindServer, "payments", calleeID, callerID)
	callee.Partition = partition

	accumulator.Observe(baseTime, caller)
	accumulator.Observe(baseTime, callee)
}
