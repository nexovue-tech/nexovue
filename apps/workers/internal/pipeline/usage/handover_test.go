package usage

import (
	"context"
	"testing"
)

// usageKept is the partition this worker goes on owning through the rebalance;
// usageMoved is the one the group hands to another worker.
const (
	usageKept  int32 = 0
	usageMoved int32 = 1
)

// The billing half of ADR 0090's root, asserted on the counters the store
// actually holds.
//
// The meter sums across partitions into one tenant-day, so a partition handed
// to another worker leaves its bytes and spans behind in the unflushed bucket.
// The next flush writes them and advances that partition's watermark, while the
// worker that took the partition over has already read the old watermark and
// counted the same records. `usage_daily` adds on conflict, so the two land as a
// sum — and this is the number a customer is billed on and the free tier is cut
// off by (ADR 0083).
//
// The store's own guard does not stop it. `WHERE counted_through < excluded`
// gates a partition's row, but one flush carries every partition it owns: the
// kept partition advances, so the transaction proceeds and adds the revoked
// partition's counters with it.
func TestARevokedPartitionsUsageIsNotBilledTwice(t *testing.T) {
	store := newRecordedStore()
	losing, gaining := testCounting(store), testCounting(store)
	ctx := context.Background()

	const keptSpans, movedSpans = 3, 5
	moved := batch(t, usageMoved, 100, movedSpans)
	if err := losing.absorb(ctx, batch(t, usageKept, 100, keptSpans), testReceivedAt); err != nil {
		t.Fatalf("absorbing the kept partition: %v", err)
	}
	if err := losing.absorb(ctx, moved, testReceivedAt); err != nil {
		t.Fatalf("absorbing the partition about to move: %v", err)
	}

	losing.disown(testTopic, []int32{usageMoved})

	// The gaining worker reads the durable watermark on assignment, which is
	// before the losing worker's next flush — that ordering is the whole race.
	if err := gaining.absorb(ctx, moved, testReceivedAt); err != nil {
		t.Fatalf("the gaining worker absorbing the moved partition: %v", err)
	}
	if err := gaining.flush(ctx); err != nil {
		t.Fatalf("the gaining worker flushing: %v", err)
	}
	if err := losing.flush(ctx); err != nil {
		t.Fatalf("the losing worker flushing: %v", err)
	}

	if got := store.spans(); got != keptSpans+movedSpans {
		t.Errorf("usage_daily holds %d spans, want %d — every span metered once. "+
			"The moved partition's records are uncommitted, so the worker that "+
			"took it over meters them, and this one must not",
			got, keptSpans+movedSpans)
	}
	if got := store.rows[testDayKey()].Bytes; got != meteredBytes(t, keptSpans)+meteredBytes(t, movedSpans) {
		t.Errorf("usage_daily holds %d bytes, want %d: bytes are what the free tier "+
			"ends on, so a handover that doubles them cuts a customer off early",
			got, meteredBytes(t, keptSpans)+meteredBytes(t, movedSpans))
	}
}

// Both halves or neither, exactly as the span buffer needs (ADR 0090). Dropping
// the counters while the in-process watermark still remembers their offsets
// would make this worker skip those records if the partition came back — and
// they were dropped, not counted, so that is an under-bill rather than the
// over-bill this exists to stop.
func TestARevokedPartitionsUsageWatermarkIsForgottenWithIt(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)

	if err := counter.absorb(context.Background(), batch(t, usageMoved, 100, 3), testReceivedAt); err != nil {
		t.Fatalf("absorbing: %v", err)
	}
	counter.disown(testTopic, []int32{usageMoved})

	if !counter.seen.FirstDelivery(testTopic, usageMoved, 100) {
		t.Error("the moved partition's record is still remembered as metered; " +
			"if the partition came back, nothing would ever bill for it")
	}
	if _, held := counter.reached[usageMoved]; held {
		t.Error("the moved partition is still in the unflushed progress; the next " +
			"flush would advance its watermark over records it no longer holds")
	}
}

// A revoke names some partitions, not all of them. What it does not name is
// still metered.
func TestUsageSurvivesARevokeThatNamesAnotherPartition(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)
	ctx := context.Background()

	if err := counter.absorb(ctx, batch(t, usageKept, 100, 4), testReceivedAt); err != nil {
		t.Fatalf("absorbing: %v", err)
	}
	counter.disown(testTopic, []int32{usageMoved})
	if err := counter.flush(ctx); err != nil {
		t.Fatalf("flushing: %v", err)
	}

	if got := store.spans(); got != 4 {
		t.Errorf("usage_daily holds %d spans, want 4: a revoke of a partition this "+
			"worker never held must meter nothing away", got)
	}
}

// The partitions a tenant-day was metered on are provenance, not part of the
// bill: they are kept apart in memory so a handover can drop one, and summed
// back together before anything reaches `usage_daily`. A row per partition would
// multiply the flush by the topic's partition count and take that many row locks.
func TestPartitionSharesOfOneTenantDayAreOneRow(t *testing.T) {
	meter := NewMeter()
	meter.Observe(usageKept, testOrg, testProject, testReceivedAt,
		Metered{Counts: Counts{Spans: 3, Bytes: 100}, Services: []string{"checkout"}})
	meter.Observe(usageMoved, testOrg, testProject, testReceivedAt,
		Metered{Counts: Counts{Spans: 4, Bytes: 200}, Services: []string{"payments"}})

	rows := meter.Drain()
	if len(rows) != 1 {
		t.Fatalf("drained %d rows, want one tenant-day: %+v", len(rows), rows)
	}
	if rows[0].Spans != 7 || rows[0].Bytes != 300 {
		t.Errorf("drained %d spans and %d bytes, want 7 and 300", rows[0].Spans, rows[0].Bytes)
	}
	if len(rows[0].Services) != 2 {
		t.Errorf("drained services %v, want both partitions' names", rows[0].Services)
	}
}

// meteredBytes is the OTLP payload one record of n spans carries, which is what
// the meter bills. Read back through the meter itself, so the expectation
// cannot drift from the message the batches are built from.
func meteredBytes(t *testing.T, spans int) int64 {
	t.Helper()
	_, metered, err := Spans.meter(spanMessage(t, spans, testReceivedAt))
	if err != nil {
		t.Fatalf("metering the reference message: %v", err)
	}
	return metered.Bytes
}
