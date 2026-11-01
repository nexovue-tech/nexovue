package ingeststate_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

var (
	progressStart = time.Date(2026, 11, 1, 9, 0, 0, 0, time.UTC)
	spansZero     = ingeststate.Partition{Topic: "spans-raw", ID: 0}
	spansOne      = ingeststate.Partition{Topic: "spans-raw", ID: 1}
	zeroOnly      = []ingeststate.Partition{spansZero}
	both          = []ingeststate.Partition{spansZero, spansOne}
)

// fakeProgress stands in for the KeeperMap table, and outlives the Progress
// that wrote it, as Keeper does.
type fakeProgress struct {
	rows     ingeststate.Marks
	failSave error
}

func newFakeProgress() *fakeProgress { return &fakeProgress{rows: make(ingeststate.Marks)} }

func (f *fakeProgress) Load(context.Context) (ingeststate.Written, error) {
	loaded := make(ingeststate.Written, len(f.rows))
	for partition, mark := range f.rows {
		loaded[partition] = mark.CountedThrough
	}
	return loaded, nil
}

func (f *fakeProgress) Save(_ context.Context, marks ingeststate.Marks) error {
	if f.failSave != nil {
		return f.failSave
	}
	for partition, mark := range marks {
		f.rows[partition] = mark
	}
	return nil
}

func progressOver(t *testing.T, store ingeststate.ProgressStore) *ingeststate.Progress {
	t.Helper()
	progress := ingeststate.NewProgress(store, ingeststate.MaxProgressHold)
	if err := progress.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh: %v", err)
	}
	return progress
}

func mark(offset int64, minutes int) ingeststate.Mark {
	return ingeststate.Mark{
		CountedThrough: offset,
		ReceivedAt:     progressStart.Add(time.Duration(minutes) * time.Minute),
	}
}

func record(
	t *testing.T,
	progress *ingeststate.Progress,
	at time.Duration,
	marks ingeststate.Marks,
	refused []ingeststate.Partition,
) ingeststate.Recorded {
	t.Helper()
	recorded, err := progress.Record(t.Context(), progressStart.Add(at), marks, refused)
	if err != nil {
		t.Fatalf("Record: %v", err)
	}
	return recorded
}

func TestAnAcknowledgedSettlementRecordsEachPartitionsMark(t *testing.T) {
	store := newFakeProgress()
	progress := progressOver(t, store)

	record(t, progress, 0, ingeststate.Marks{spansZero: mark(10, 1), spansOne: mark(20, 2)}, nil)

	if got := store.rows[spansZero]; got != mark(10, 1) {
		t.Errorf("partition 0 recorded %+v, want %+v", got, mark(10, 1))
	}
	if got := store.rows[spansOne]; got != mark(20, 2) {
		t.Errorf("partition 1 recorded %+v, want %+v", got, mark(20, 2))
	}
}

// One derived insert covers every partition in a settlement, so a refusal
// leaves a hole in all of their minutes, and all of them must read delayed
// rather than be judged on the hole (C22).
func TestARefusalHoldsEveryPartitionInTheSettlement(t *testing.T) {
	store := newFakeProgress()
	progress := progressOver(t, store)
	record(t, progress, 0, ingeststate.Marks{spansZero: mark(10, 1), spansOne: mark(20, 1)}, nil)

	held := record(t, progress, time.Minute,
		ingeststate.Marks{spansZero: mark(30, 2), spansOne: mark(40, 2)}, both)
	if held.Written != 0 || held.Held != 2 {
		t.Errorf("recorded %+v, want both partitions held and nothing written", held)
	}

	// The next settlement is accepted, but its marks would carry the refused
	// minute past the scheduler, so they wait with the hold.
	record(t, progress, 2*time.Minute,
		ingeststate.Marks{spansZero: mark(50, 3), spansOne: mark(60, 3)}, nil)
	if store.rows[spansZero].CountedThrough != 10 || store.rows[spansOne].CountedThrough != 20 {
		t.Errorf("progress moved past a refused settlement: %+v", store.rows)
	}
}

// A store that keeps refusing must not switch detection off for good: after
// MaxProgressHold the newest mark is written, and the release is reported.
func TestAHoldEndsAfterThirtyMinutes(t *testing.T) {
	store := newFakeProgress()
	progress := progressOver(t, store)
	record(t, progress, 0, ingeststate.Marks{spansZero: mark(10, 1)}, zeroOnly)
	record(t, progress, 10*time.Minute, ingeststate.Marks{spansZero: mark(30, 11)}, zeroOnly)

	if still := record(t, progress, ingeststate.MaxProgressHold-time.Second, nil, nil); still.Written != 0 {
		t.Fatalf("a hold ended after %v, before its bound", ingeststate.MaxProgressHold-time.Second)
	}
	if _, recorded := store.rows[spansZero]; recorded {
		t.Fatalf("progress was written while held: %+v", store.rows[spansZero])
	}

	// No new settlement arrives: a partition gone quiet must still be released.
	released := record(t, progress, ingeststate.MaxProgressHold, nil, nil)
	if len(released.Released) != 1 || released.Released[0] != spansZero {
		t.Errorf("released %v, want partition 0 reported", released.Released)
	}
	if got := store.rows[spansZero]; got != mark(30, 11) {
		t.Errorf("partition 0 recorded %+v after its hold, want the newest mark %+v", got, mark(30, 11))
	}

	// Released, the partition records normally again.
	record(t, progress, ingeststate.MaxProgressHold+time.Minute, ingeststate.Marks{spansZero: mark(40, 32)}, nil)
	if got := store.rows[spansZero].CountedThrough; got != 40 {
		t.Errorf("partition 0 at %d after the hold, want 40", got)
	}
}

// A partition that left for another worker is that worker's to record. A hold
// this process still had for it must not write when it ends (C1).
func TestARevokedPartitionIsNotWritten(t *testing.T) {
	store := newFakeProgress()
	progress := progressOver(t, store)
	record(t, progress, 0, ingeststate.Marks{spansZero: mark(10, 1), spansOne: mark(20, 1)}, both)

	progress.Forget([]ingeststate.Partition{spansOne})
	record(t, progress, ingeststate.MaxProgressHold, nil, nil)

	if _, written := store.rows[spansOne]; written {
		t.Errorf("a revoked partition's progress was written: %+v", store.rows[spansOne])
	}
	if got := store.rows[spansZero].CountedThrough; got != 10 {
		t.Errorf("the partition still owned recorded %d, want 10", got)
	}
}

// After a restart or a handover, a replayed settlement can name offsets the
// store has already recorded past. Writing it would unsettle minutes the
// scheduler has already judged.
func TestAReplayNeverMovesProgressBackwards(t *testing.T) {
	store := newFakeProgress()
	record(t, progressOver(t, store), 0, ingeststate.Marks{spansZero: mark(100, 5)}, nil)

	restarted := progressOver(t, store)
	replayed := record(t, restarted, time.Minute, ingeststate.Marks{spansZero: mark(60, 3)}, nil)

	if replayed.Written != 0 {
		t.Errorf("a replay wrote %d marks, want none", replayed.Written)
	}
	if got := store.rows[spansZero]; got != mark(100, 5) {
		t.Errorf("progress moved backwards to %+v", got)
	}
}

// A failed write changes nothing in memory, so the loop's retry tries the same
// thing again rather than a hold that was already cleared.
func TestAFailedWriteKeepsTheHold(t *testing.T) {
	store := newFakeProgress()
	progress := progressOver(t, store)
	record(t, progress, 0, ingeststate.Marks{spansZero: mark(10, 1)}, zeroOnly)

	store.failSave = errors.New("keeper unavailable")
	if _, err := progress.Record(t.Context(), progressStart.Add(ingeststate.MaxProgressHold), nil, nil); err == nil {
		t.Fatal("Record succeeded with the store down")
	}

	store.failSave = nil
	released := record(t, progress, ingeststate.MaxProgressHold, nil, nil)
	if len(released.Released) != 1 || store.rows[spansZero].CountedThrough != 10 {
		t.Errorf("the retry released %v and recorded %+v, want partition 0 at 10",
			released.Released, store.rows[spansZero])
	}
}

// A drain writes every closed minute's rows, but the marks cover only the
// reached prefix, which can still wait on edges. A refusal with no mark for a
// partition must still hold it: otherwise the batches holding the refused
// minute reach the prefix later, unrefused, and progress passes the hole.
func TestARefusalHoldsAPartitionTheSettlementHadNoMarkFor(t *testing.T) {
	store := newFakeProgress()
	progress := progressOver(t, store)
	record(t, progress, 0, ingeststate.Marks{spansZero: mark(10, 1)}, nil)

	refused := record(t, progress, time.Minute, nil, []ingeststate.Partition{spansZero})
	if refused.Held != 1 {
		t.Fatalf("recorded %+v, want partition 0 held though the prefix had no mark for it", refused)
	}

	record(t, progress, 2*time.Minute, ingeststate.Marks{spansZero: mark(30, 2)}, nil)
	if got := store.rows[spansZero].CountedThrough; got != 10 {
		t.Errorf("progress moved to %d, past the refused minute, while held at 10", got)
	}

	// The limit still applies, and the newest mark reached meanwhile is written.
	record(t, progress, time.Minute+ingeststate.MaxProgressHold, nil, nil)
	if got := store.rows[spansZero]; got != mark(30, 2) {
		t.Errorf("partition 0 recorded %+v after its hold, want %+v", got, mark(30, 2))
	}
}

// A hold that ends with no mark reached in the meantime has nothing to write.
func TestAHoldWithNoMarkEndsWithoutWriting(t *testing.T) {
	store := newFakeProgress()
	progress := progressOver(t, store)

	record(t, progress, 0, nil, []ingeststate.Partition{spansOne})
	ended := record(t, progress, ingeststate.MaxProgressHold, nil, nil)

	if _, written := store.rows[spansOne]; written {
		t.Errorf("a hold that reached no mark wrote %+v", store.rows[spansOne])
	}
	if len(ended.Released) != 1 || ended.Released[0] != spansOne || ended.Held != 0 {
		t.Errorf("recorded %+v, want partition 1's hold ended and reported", ended)
	}
}
