package ingeststate_test

import (
	"context"
	"errors"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

var partition = ingeststate.Partition{Topic: "spans-raw", ID: 0}

// fakeStore stands in for the KeeperMap table, and survives a "restart" the way
// Keeper does: the rows outlive the guard that wrote them.
type fakeStore struct {
	rows     map[ingeststate.Partition]ingeststate.State
	loads    int
	writes   int
	failLoad error
	failSave error
}

func newFakeStore() *fakeStore {
	return &fakeStore{rows: make(map[ingeststate.Partition]ingeststate.State)}
}

func (s *fakeStore) Load(context.Context) (map[ingeststate.Partition]ingeststate.State, error) {
	s.loads++
	if s.failLoad != nil {
		return nil, s.failLoad
	}
	loaded := make(map[ingeststate.Partition]ingeststate.State, len(s.rows))
	for key, state := range s.rows {
		loaded[key] = state
	}
	return loaded, nil
}

func (s *fakeStore) Save(_ context.Context, states map[ingeststate.Partition]ingeststate.State) error {
	if s.failSave != nil {
		return s.failSave
	}
	for key, state := range states {
		// The counter table holds no span watermark, so a reload of this row
		// alone would forget it. Keeping the stored one models the two tables.
		state.WrittenThrough = s.rows[key].WrittenThrough
		s.rows[key] = state
	}
	return nil
}

func (s *fakeStore) SaveWritten(_ context.Context, written ingeststate.Written) error {
	if s.failSave != nil {
		return s.failSave
	}
	s.writes++
	for key, offset := range written {
		row, known := s.rows[key]
		if !known {
			row = ingeststate.State{
				Range: ingeststate.Range{Min: 0, Max: -1},
				Flag:  ingeststate.After,
			}
		}
		row.WrittenThrough = offset
		s.rows[key] = row
	}
	return nil
}

// settleGuard runs one complete settlement and fails the test if any step does.
func settleGuard(t *testing.T, guard *ingeststate.Guard, reached ingeststate.Range) {
	t.Helper()
	settlement := guard.Advance(map[ingeststate.Partition]ingeststate.Range{partition: reached})
	if err := guard.Begin(t.Context(), settlement, nil); err != nil {
		t.Fatalf("Begin: %v", err)
	}
	if err := guard.Complete(t.Context(), settlement); err != nil {
		t.Fatalf("Complete: %v", err)
	}
}

func refresh(t *testing.T, guard *ingeststate.Guard) {
	t.Helper()
	if err := guard.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh: %v", err)
	}
}

func counting(guard *ingeststate.Guard, incoming ingeststate.Range) ingeststate.Decision {
	return guard.Counting(map[ingeststate.Partition]ingeststate.Range{partition: incoming})[partition]
}

// The measured failure, in one test: counters reach the store, the
// offsets never commit, and the replay after the restart must not count them
// again.
func TestAReplayAfterAConfirmedSettlementIsNotCountedAgain(t *testing.T) {
	store := newFakeStore()

	before := ingeststate.NewGuard(store)
	refresh(t, before)
	settleGuard(t, before, ingeststate.Range{Min: 0, Max: 99})

	// The store restarts and the worker with it; only what Keeper holds is left.
	after := ingeststate.NewGuard(store)
	refresh(t, after)
	decision := counting(after, ingeststate.Range{Min: 0, Max: 149})

	for _, offset := range []int64{0, 50, 99} {
		if decision.Counts(offset) {
			t.Errorf("offset %d was counted twice across the restart", offset)
		}
	}
	for _, offset := range []int64{100, 149} {
		if !decision.Counts(offset) {
			t.Errorf("offset %d was never counted and the replay skipped it", offset)
		}
	}
}

// The ambiguous half: the insert was in flight when everything died, so nobody
// knows whether it landed. Counting again risks a duplicate that ClickHouse's
// insert deduplication drops; skipping guarantees a hole nothing can fill.
func TestAnInterruptedSettlementIsCountedAgain(t *testing.T) {
	store := newFakeStore()

	before := ingeststate.NewGuard(store)
	refresh(t, before)
	settlement := before.Advance(map[ingeststate.Partition]ingeststate.Range{
		partition: {Min: 0, Max: 99},
	})
	if err := before.Begin(t.Context(), settlement, nil); err != nil {
		t.Fatalf("Begin: %v", err)
	}

	after := ingeststate.NewGuard(store)
	refresh(t, after)
	decision := counting(after, ingeststate.Range{Min: 0, Max: 149})

	if !decision.Ambiguous {
		t.Error("an unconfirmed settlement was not reported as ambiguous")
	}
	for _, offset := range []int64{0, 99, 149} {
		if !decision.Counts(offset) {
			t.Errorf("offset %d was skipped although its settlement never confirmed", offset)
		}
	}
}

func TestAdvanceRecordsOnlyWhatIsNewlyExact(t *testing.T) {
	cases := map[string]struct {
		settled   *ingeststate.State
		reached   ingeststate.Range
		wantRange ingeststate.Range
		wantWrite bool
	}{
		"nothing settled yet": {
			settled:   nil,
			reached:   ingeststate.Range{Min: 0, Max: 99},
			wantRange: ingeststate.Range{Min: 0, Max: 99},
			wantWrite: true,
		},
		"counters moved past a confirmed range": {
			settled:   &ingeststate.State{Range: ingeststate.Range{Min: 0, Max: 99}, Flag: ingeststate.After},
			reached:   ingeststate.Range{Min: 40, Max: 199},
			wantRange: ingeststate.Range{Min: 100, Max: 199},
			wantWrite: true,
		},
		"counters have not moved": {
			settled:   &ingeststate.State{Range: ingeststate.Range{Min: 0, Max: 99}, Flag: ingeststate.After},
			reached:   ingeststate.Range{Min: 40, Max: 99},
			wantWrite: false,
		},
		// The unconfirmed range was counted again into this block, so confirming
		// now has to cover it rather than leaving it behind unresolved.
		"an unconfirmed range is absorbed": {
			settled:   &ingeststate.State{Range: ingeststate.Range{Min: 50, Max: 99}, Flag: ingeststate.Before},
			reached:   ingeststate.Range{Min: 50, Max: 199},
			wantRange: ingeststate.Range{Min: 50, Max: 199},
			wantWrite: true,
		},
		// Confirming past what the counters have reached would claim offsets
		// exact that nothing has counted, which is a hole rather than a copy.
		"an unconfirmed range is only partly recounted": {
			settled:   &ingeststate.State{Range: ingeststate.Range{Min: 50, Max: 199}, Flag: ingeststate.Before},
			reached:   ingeststate.Range{Min: 60, Max: 120},
			wantRange: ingeststate.Range{Min: 50, Max: 120},
			wantWrite: true,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			store := newFakeStore()
			if tc.settled != nil {
				store.rows[partition] = *tc.settled
			}
			guard := ingeststate.NewGuard(store)
			refresh(t, guard)

			settlement := guard.Advance(map[ingeststate.Partition]ingeststate.Range{
				partition: tc.reached,
			})
			got, written := settlement[partition]
			if written != tc.wantWrite {
				t.Fatalf("recorded = %v, want %v (settlement %+v)", written, tc.wantWrite, settlement)
			}
			if written && got != tc.wantRange {
				t.Errorf("range = %+v, want %+v", got, tc.wantRange)
			}
		})
	}
}

func TestAReassignedPartitionIsReadBackBeforeItIsCounted(t *testing.T) {
	store := newFakeStore()
	guard := ingeststate.NewGuard(store)

	refresh(t, guard)
	if store.loads != 1 {
		t.Fatalf("loads = %d, want the first refresh to read the store", store.loads)
	}
	refresh(t, guard)
	if store.loads != 1 {
		t.Errorf("loads = %d, want a fresh cache to be left alone", store.loads)
	}

	// Another worker owned the partition and counted through offset 500.
	store.rows[partition] = ingeststate.State{
		Range: ingeststate.Range{Min: 0, Max: 500},
		Flag:  ingeststate.After,
	}
	guard.Invalidate()
	refresh(t, guard)

	if store.loads != 2 {
		t.Errorf("loads = %d, want a reassignment to force a re-read", store.loads)
	}
	if counting(guard, ingeststate.Range{Min: 400, Max: 600}).Counts(500) {
		t.Error("an offset the previous owner counted was counted again after the handover")
	}
}

// Nothing may be cached that the store did not accept: a cache ahead of Keeper
// would skip offsets no one ever counted.
func TestAFailedWriteLeavesTheCacheAlone(t *testing.T) {
	store := newFakeStore()
	store.failSave = errors.New("clickhouse is down")
	guard := ingeststate.NewGuard(store)
	refresh(t, guard)

	settlement := guard.Advance(map[ingeststate.Partition]ingeststate.Range{
		partition: {Min: 0, Max: 99},
	})
	if err := guard.Begin(t.Context(), settlement, nil); err == nil {
		t.Fatal("Begin succeeded although the store refused the write")
	}
	if _, known := guard.Settled(partition); known {
		t.Error("a refused write was cached anyway")
	}
}

func TestAFailedReadIsReportedAndRetried(t *testing.T) {
	store := newFakeStore()
	store.failLoad = errors.New("clickhouse is down")
	guard := ingeststate.NewGuard(store)

	if err := guard.Refresh(t.Context()); err == nil {
		t.Fatal("Refresh succeeded although the store was unreadable")
	}
	store.failLoad = nil
	refresh(t, guard)
	if store.loads != 2 {
		t.Errorf("loads = %d, want the failed read to leave the cache due another", store.loads)
	}
}

func TestAnEmptySettlementCostsNoWrite(t *testing.T) {
	store := newFakeStore()
	store.failSave = errors.New("the store must not be called at all")
	guard := ingeststate.NewGuard(store)
	refresh(t, guard)

	if err := guard.Begin(t.Context(), ingeststate.Settlement{}, nil); err != nil {
		t.Errorf("Begin on an empty settlement: %v", err)
	}
	if err := guard.Complete(t.Context(), nil); err != nil {
		t.Errorf("Complete on an empty settlement: %v", err)
	}
}

// storing runs one span-watermark write and fails the test if it does.
func storing(t *testing.T, guard *ingeststate.Guard, reached ingeststate.Range) {
	t.Helper()
	written := guard.Writing(map[ingeststate.Partition]ingeststate.Range{partition: reached})
	if err := guard.Begin(t.Context(), nil, written); err != nil {
		t.Fatalf("Begin: %v", err)
	}
}

func decide(guard *ingeststate.Guard, incoming ingeststate.Range) ingeststate.Decision {
	return guard.Counting(map[ingeststate.Partition]ingeststate.Range{partition: incoming})[partition]
}

// The defect this package was extended for: the spans reach ClickHouse, the
// offsets never commit, and the replay after the restart must not write them
// again. Measured before the fix, 6135 of 14980 distinct spans over six hours
// had more than one copy.
func TestSpansWrittenBeforeARestartAreNotWrittenAgain(t *testing.T) {
	store := newFakeStore()

	before := ingeststate.NewGuard(store)
	refresh(t, before)
	storing(t, before, ingeststate.Range{Min: 0, Max: 99})

	// The worker restarts; only what Keeper holds is left.
	after := ingeststate.NewGuard(store)
	refresh(t, after)
	decision := decide(after, ingeststate.Range{Min: 0, Max: 149})

	for _, offset := range []int64{0, 50, 99} {
		if decision.Stores(offset) {
			t.Errorf("offset %d was written twice across the restart", offset)
		}
	}
	for _, offset := range []int64{100, 149} {
		if !decision.Stores(offset) {
			t.Errorf("offset %d was never written and the replay skipped it", offset)
		}
	}
}

// The band the watermark exists for: spans already stored, counters not yet
// exact. Both answers have to come out of the same decision.
func TestAReplayCanBeCountedAndNotWritten(t *testing.T) {
	store := newFakeStore()
	guard := ingeststate.NewGuard(store)
	refresh(t, guard)

	settleGuard(t, guard, ingeststate.Range{Min: 0, Max: 49})
	storing(t, guard, ingeststate.Range{Min: 0, Max: 99})

	reopened := ingeststate.NewGuard(store)
	refresh(t, reopened)
	decision := decide(reopened, ingeststate.Range{Min: 0, Max: 149})

	if decision.Stores(75) {
		t.Error("offset 75 was written again although its spans are stored")
	}
	if !decision.Counts(75) {
		t.Error("offset 75 was not counted although its counters are not yet exact")
	}
}

// A counter settlement writes the other table. It must not walk the span
// watermark back to "nothing written", which would duplicate the next replay.
func TestASettlementDoesNotForgetTheSpanWatermark(t *testing.T) {
	store := newFakeStore()
	guard := ingeststate.NewGuard(store)
	refresh(t, guard)

	storing(t, guard, ingeststate.Range{Min: 0, Max: 99})
	settleGuard(t, guard, ingeststate.Range{Min: 0, Max: 49})

	if decide(guard, ingeststate.Range{Min: 0, Max: 149}).Stores(50) {
		t.Error("the settlement forgot the span watermark held in the same cache entry")
	}
	reopened := ingeststate.NewGuard(store)
	refresh(t, reopened)
	if decide(reopened, ingeststate.Range{Min: 0, Max: 149}).Stores(50) {
		t.Error("the settlement forgot the span watermark in the store")
	}
}

// A watermark that has not moved must cost no write. This is the hot path: it
// runs every turn of the consume loop.
func TestAnUnmovedSpanWatermarkCostsNoWrite(t *testing.T) {
	store := newFakeStore()
	guard := ingeststate.NewGuard(store)
	refresh(t, guard)

	storing(t, guard, ingeststate.Range{Min: 0, Max: 99})
	if store.writes != 1 {
		t.Fatalf("writes = %d, want the first watermark stored", store.writes)
	}
	storing(t, guard, ingeststate.Range{Min: 40, Max: 99})
	if store.writes != 1 {
		t.Errorf("writes = %d, want a watermark that did not move to write nothing", store.writes)
	}
}

// Nothing may be cached that the store did not accept: a span watermark ahead
// of Keeper would skip spans no one ever wrote.
func TestAFailedWatermarkWriteLeavesTheCacheAlone(t *testing.T) {
	store := newFakeStore()
	store.failSave = errors.New("clickhouse is down")
	guard := ingeststate.NewGuard(store)
	refresh(t, guard)

	written := guard.Writing(map[ingeststate.Partition]ingeststate.Range{
		partition: {Min: 0, Max: 99},
	})
	if err := guard.Begin(t.Context(), nil, written); err == nil {
		t.Fatal("Begin succeeded although the store refused the write")
	}
	if decide(guard, ingeststate.Range{Min: 0, Max: 149}).Stores(50) == false {
		t.Error("a refused watermark write was cached anyway, and offset 50 would be skipped")
	}
}
