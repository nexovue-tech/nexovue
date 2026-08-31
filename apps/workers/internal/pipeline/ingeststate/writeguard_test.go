package ingeststate_test

import (
	"context"
	"errors"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

var logPartition = ingeststate.Partition{Topic: "logs-raw", ID: 0}

// fakeWatermarks stands in for the KeeperMap table, and survives a "restart"
// the way Keeper does: the rows outlive the guard that wrote them.
type fakeWatermarks struct {
	rows     ingeststate.Written
	loads    int
	writes   int
	failLoad error
	failSave error
}

func newFakeWatermarks() *fakeWatermarks {
	return &fakeWatermarks{rows: make(ingeststate.Written)}
}

func (f *fakeWatermarks) Load(context.Context) (ingeststate.Written, error) {
	f.loads++
	if f.failLoad != nil {
		return nil, f.failLoad
	}
	loaded := make(ingeststate.Written, len(f.rows))
	for key, offset := range f.rows {
		loaded[key] = offset
	}
	return loaded, nil
}

func (f *fakeWatermarks) Save(_ context.Context, written ingeststate.Written) error {
	if f.failSave != nil {
		return f.failSave
	}
	f.writes++
	for key, offset := range written {
		f.rows[key] = offset
	}
	return nil
}

// positions builds one partition's batch coordinates, which is what the
// pipeline hands the guard.
func positions(offsets ...int64) []consume.Position {
	at := make([]consume.Position, 0, len(offsets))
	for _, offset := range offsets {
		at = append(at, consume.Position{
			Topic:     logPartition.Topic,
			Partition: logPartition.ID,
			Offset:    offset,
		})
	}
	return at
}

func refreshWrites(t *testing.T, guard *ingeststate.WriteGuard) {
	t.Helper()
	if err := guard.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh: %v", err)
	}
}

func advance(t *testing.T, guard *ingeststate.WriteGuard, at []consume.Position) {
	t.Helper()
	if err := guard.Advance(t.Context(), at); err != nil {
		t.Fatalf("Advance: %v", err)
	}
}

// The defect this exists for: the rows reach ClickHouse, the offsets never
// commit, and the replay after an abrupt restart must not write them again.
// Measured before the fix over 60 SIGKILL restarts under load, 49,580 of
// 905,020 distinct log rows had two copies.
func TestRowsWrittenBeforeARestartAreNotWrittenAgain(t *testing.T) {
	store := newFakeWatermarks()

	before := ingeststate.NewWriteGuard(store)
	refreshWrites(t, before)
	advance(t, before, positions(10, 11, 12))

	// The restart: a new guard over the same durable rows.
	after := ingeststate.NewWriteGuard(store)
	refreshWrites(t, after)

	for _, offset := range []int64{10, 11, 12} {
		if after.Storing(logPartition, offset) {
			t.Errorf("offset %d would be written again; its rows are already in the store", offset)
		}
	}
	if !after.Storing(logPartition, 13) {
		t.Error("offset 13 was skipped; nothing ever wrote its rows")
	}
}

// The direction that loses data. A partition the guard knows nothing about must
// be written: a duplicate row can be collapsed later and a missing one cannot,
// and "no data loss between receiver and storage" is a phase-1 invariant while
// duplication is not.
func TestAnUnknownPartitionIsWritten(t *testing.T) {
	guard := ingeststate.NewWriteGuard(newFakeWatermarks())
	refreshWrites(t, guard)

	if !guard.Storing(ingeststate.Partition{Topic: "logs-raw", ID: 7}, 0) {
		t.Error("offset 0 of an unknown partition was skipped; that is a hole, not a duplicate")
	}
}

// Offset zero is a real offset. A watermark that started at zero rather than
// below it would skip the very first record of a partition.
func TestTheFirstRecordOfAPartitionIsWritten(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)
	refreshWrites(t, guard)

	if !guard.Storing(logPartition, 0) {
		t.Fatal("offset 0 was skipped before anything was written")
	}
	advance(t, guard, positions(0))
	if guard.Storing(logPartition, 0) {
		t.Error("offset 0 was offered again after its rows were acknowledged")
	}
}

// The hot path: this runs on every insert, so a watermark that has not moved
// must cost no round trip to Keeper.
func TestAnUnmovedWatermarkCostsNoWrite(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)
	refreshWrites(t, guard)

	advance(t, guard, positions(4, 5))
	if store.writes != 1 {
		t.Fatalf("writes = %d after the first advance, want 1", store.writes)
	}
	advance(t, guard, positions(4, 5))
	if store.writes != 1 {
		t.Errorf("writes = %d after re-advancing to the same offset, want 1", store.writes)
	}
}

// Each partition carries its own watermark. One partition's progress must not
// skip another's records.
func TestPartitionsAdvanceIndependently(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)
	refreshWrites(t, guard)

	advance(t, guard, positions(9))
	other := ingeststate.Partition{Topic: "logs-raw", ID: 1}
	if !guard.Storing(other, 0) {
		t.Error("partition 1 offset 0 was skipped because partition 0 had advanced")
	}
}

// Topics share the table, so a log watermark must not silence a metric record.
// This is what makes one table safe for both signals.
func TestTopicsShareTheTableWithoutColliding(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)
	refreshWrites(t, guard)

	advance(t, guard, positions(99))
	metrics := ingeststate.Partition{Topic: "metrics-raw", ID: 0}
	if !guard.Storing(metrics, 0) {
		t.Error("a metrics record was skipped because the logs topic had advanced")
	}
}

// A partition can arrive from another worker whose writes this process never
// saw. Writing on a stale cache is what duplicates them.
func TestAReassignedPartitionIsReadBackBeforeAnythingIsWritten(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)
	refreshWrites(t, guard)

	// Another worker owned the partition and wrote through offset 40.
	store.rows[logPartition] = 40

	// Refresh alone does not re-read: nothing has said the cache is stale, so
	// offset 40 would be written a second time. That is the hazard.
	refreshWrites(t, guard)
	if !guard.Storing(logPartition, 40) {
		t.Fatal("the cache already knew offset 40 without being invalidated; the test proves nothing")
	}

	guard.Invalidate()
	refreshWrites(t, guard)
	if guard.Storing(logPartition, 40) {
		t.Error("offset 40 was written again after the reassignment was read back")
	}
}

// Refresh is called on every batch and must cost a read only when it is due.
func TestRefreshReadsOnlyWhenDue(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)

	refreshWrites(t, guard)
	refreshWrites(t, guard)
	if store.loads != 1 {
		t.Fatalf("loads = %d, want 1: the second refresh was not due", store.loads)
	}

	guard.Invalidate()
	refreshWrites(t, guard)
	if store.loads != 2 {
		t.Errorf("loads = %d after Invalidate, want 2", store.loads)
	}
}

// Nothing may be cached that the store did not accept: a cache ahead of Keeper
// would skip rows nobody ever wrote.
func TestAFailedWatermarkAdvanceLeavesTheCacheAlone(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)
	refreshWrites(t, guard)

	store.failSave = errors.New("keeper is down")
	if err := guard.Advance(t.Context(), positions(21)); err == nil {
		t.Fatal("Advance succeeded while the store was failing")
	}
	if !guard.Storing(logPartition, 21) {
		t.Error("offset 21 was cached as written although the store refused it")
	}
}

// A read that fails must be reported rather than leaving an empty cache behind,
// which would read as "nothing written" and duplicate the whole replay.
func TestAFailedWatermarkReadIsReportedAndRetried(t *testing.T) {
	store := newFakeWatermarks()
	store.rows[logPartition] = 12
	guard := ingeststate.NewWriteGuard(store)

	store.failLoad = errors.New("clickhouse is down")
	if err := guard.Refresh(t.Context()); err == nil {
		t.Fatal("Refresh succeeded while the store was failing")
	}

	store.failLoad = nil
	refreshWrites(t, guard)
	if guard.Storing(logPartition, 12) {
		t.Error("offset 12 was written again; the retried read did not take")
	}
}

// An advance over no records must cost no write. A batch every record of which
// was already stored reaches this.
func TestAnEmptyAdvanceCostsNoWrite(t *testing.T) {
	store := newFakeWatermarks()
	guard := ingeststate.NewWriteGuard(store)
	refreshWrites(t, guard)

	advance(t, guard, nil)
	if store.writes != 0 {
		t.Errorf("writes = %d for an empty advance, want 0", store.writes)
	}
}
