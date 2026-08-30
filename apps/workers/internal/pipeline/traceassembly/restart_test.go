package traceassembly

import (
	"context"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

// samplingWindow is what the deployment default holds a trace for (ADR 0058).
const samplingWindow = 10 * time.Second

// The ordering the whole fix rests on: the span watermark may only claim
// offsets whose spans have actually reached ClickHouse.
//
// Claiming them early is the dangerous direction. A batch still inside its
// sampling window has spans in memory and nothing in the store, and a watermark
// covering it would make the replay after a crash skip spans nothing ever
// wrote. A duplicate row can be collapsed later; a missing span cannot be
// recovered, and "no data loss between receiver and storage" is a phase-1
// invariant.
func TestTheSpanWatermarkWaitsForTheSamplingWindow(t *testing.T) {
	polledAt := invariantStart

	var queue consume.Queue[spanStorage]
	queue.Hold(consume.Batch[spanStorage]{
		Positions: fakePositions(5),
		PolledAt:  polledAt,
		Produced:  spanStorage{spansStoredAt: polledAt.Add(samplingWindow)},
	})

	insideWindow := queue.Reached(spansStored(polledAt.Add(samplingWindow - time.Second)))
	if len(insideWindow) != 0 {
		t.Errorf("claimed %d offsets inside the sampling window, want none: those spans "+
			"are still in the buffer and a replay that skipped them would lose them",
			len(insideWindow))
	}

	windowClosed := queue.Reached(spansStored(polledAt.Add(samplingWindow)))
	if len(windowClosed) != 5 {
		t.Fatalf("claimed %d offsets once the window closed, want 5", len(windowClosed))
	}
	if reached := ingeststate.RangesOf(windowClosed); reached[spansPartition].Max != 4 {
		t.Errorf("watermark = %d, want the last offset of the batch (4)",
			reached[spansPartition].Max)
	}
}

// The watermark has to run ahead of the committed offsets, or it changes
// nothing. Measured on this deployment the gap is most of the replay: commit
// lag p50 43s and p90 72s against a 10s sampling window, because a batch waits
// for its counters' minute bucket long after its spans are stored.
func TestTheSpanWatermarkRunsAheadOfTheCommittedOffsets(t *testing.T) {
	polledAt := invariantStart
	pipeline := Pipeline{stages: stagesFor(t, 100)}

	var queue consume.Queue[spanStorage]
	queue.Hold(consume.Batch[spanStorage]{
		Positions: fakePositions(5),
		PolledAt:  polledAt,
		Produced: spanStorage{
			spansStoredAt: polledAt.Add(samplingWindow),
			// A counter bucket that has not drained yet, which is what holds
			// the offsets back long after the spans are written.
			latestMinute:     polledAt.Add(time.Minute),
			latestEdgeMinute: polledAt.Add(time.Minute),
		},
	})

	justAfterTheWindow := polledAt.Add(samplingWindow)
	if written := queue.Reached(spansStored(justAfterTheWindow)); len(written) != 5 {
		t.Errorf("the span watermark covered %d offsets, want 5", len(written))
	}
	if committable := queue.Reached(storedAt(&pipeline, justAfterTheWindow)); len(committable) != 0 {
		t.Errorf("committed %d offsets, want none: the counters have not drained, which "+
			"is exactly the band the watermark exists to cover", len(committable))
	}
}

// End to end through the guard: what a restart recovers must skip precisely the
// offsets already written and no more.
func TestARecoveredWatermarkSkipsOnlyWhatWasWritten(t *testing.T) {
	polledAt := invariantStart
	store := newRecordingStore()
	guard := ingeststate.NewGuard(store)
	if err := guard.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh: %v", err)
	}

	var queue consume.Queue[spanStorage]
	queue.Hold(consume.Batch[spanStorage]{
		Positions: fakePositions(5),
		PolledAt:  polledAt,
		Produced:  spanStorage{spansStoredAt: polledAt.Add(samplingWindow)},
	})

	written := guard.Writing(ingeststate.RangesOf(
		queue.Reached(spansStored(polledAt.Add(samplingWindow)))))
	if err := guard.Begin(t.Context(), nil, written); err != nil {
		t.Fatalf("Begin: %v", err)
	}

	// The worker restarts and the broker replays offsets 0-9.
	recovered := ingeststate.NewGuard(store)
	if err := recovered.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh after the restart: %v", err)
	}
	decision := recovered.Counting(map[ingeststate.Partition]ingeststate.Range{
		spansPartition: {Min: 0, Max: 9},
	})[spansPartition]

	for offset := range int64(5) {
		if decision.Stores(offset) {
			t.Errorf("offset %d was written twice across the restart", offset)
		}
	}
	for offset := int64(5); offset < 10; offset++ {
		if !decision.Stores(offset) {
			t.Errorf("offset %d was skipped although nothing had written it", offset)
		}
	}
}

// spansPartition is the partition fakePositions writes to.
var spansPartition = ingeststate.Partition{Topic: "spans-raw", ID: 0}

// recordingStore stands in for the two KeeperMap tables, and survives a
// "restart" the way Keeper does: the rows outlive the guard that wrote them.
type recordingStore struct {
	counted map[ingeststate.Partition]ingeststate.State
	written ingeststate.Written
}

func newRecordingStore() *recordingStore {
	return &recordingStore{
		counted: make(map[ingeststate.Partition]ingeststate.State),
		written: make(ingeststate.Written),
	}
}

func (s *recordingStore) Load(context.Context) (map[ingeststate.Partition]ingeststate.State, error) {
	loaded := make(map[ingeststate.Partition]ingeststate.State, len(s.counted))
	for partition, state := range s.counted {
		state.WrittenThrough = ingeststate.NothingWritten
		loaded[partition] = state
	}
	for partition, offset := range s.written {
		state, counted := loaded[partition]
		if !counted {
			state = ingeststate.State{
				Range: ingeststate.Range{Min: 0, Max: -1},
				Flag:  ingeststate.After,
			}
		}
		state.WrittenThrough = offset
		loaded[partition] = state
	}
	return loaded, nil
}

func (s *recordingStore) Save(
	_ context.Context,
	states map[ingeststate.Partition]ingeststate.State,
) error {
	for partition, state := range states {
		s.counted[partition] = state
	}
	return nil
}

func (s *recordingStore) SaveWritten(_ context.Context, written ingeststate.Written) error {
	for partition, offset := range written {
		s.written[partition] = offset
	}
	return nil
}
