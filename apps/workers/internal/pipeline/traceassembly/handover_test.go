package traceassembly

import (
	"context"
	"log/slog"
	"maps"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dedupe"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

// spansMoved is the partition the tests below hand to another worker;
// spansKept is the one this worker goes on owning.
const (
	spansMoved int32 = 3
	spansKept  int32 = 5
)

// pipelineForHandover is the pipeline reduced to what a rebalance callback
// touches: the stages, the in-process replay guard and the durable one. No
// ClickHouse pool, because releasing a partition makes no call out of the
// process — that is the property under test as much as anything else here.
func pipelineForHandover(t *testing.T, store ingeststate.Store) *Pipeline {
	t.Helper()
	return &Pipeline{
		stages: stagesFor(t, 100),
		seen:   dedupe.NewOffsets(),
		ingest: ingeststate.NewGuard(store),
		progress: ingeststate.NewProgress(&progressRows{rows: make(ingeststate.Marks)},
			ingeststate.MaxProgressHold),
		topic: spansPartition.Topic,
		log:   slog.New(slog.DiscardHandler),
	}
}

// progressRows stands in for the progress table.
type progressRows struct{ rows ingeststate.Marks }

func (p *progressRows) Load(context.Context) (ingeststate.Written, error) {
	return ingeststate.Written{}, nil
}

func (p *progressRows) Save(_ context.Context, marks ingeststate.Marks) error {
	maps.Copy(p.rows, marks)
	return nil
}

// absorbOn runs one trace's message through the stages as a record on the given
// partition, the way Absorb does for a record it has not seen before.
func absorbOn(t *testing.T, pipeline *Pipeline, partition int32, offset int64, index int) {
	t.Helper()
	first := pipeline.seen.FirstDelivery(pipeline.topic, partition, offset)
	if !first {
		t.Fatalf("offset %d on partition %d was refused on its first delivery", offset, partition)
	}
	if _, err := pipeline.stages.absorbMessage(invariantStart, traceMessage(t, index, true),
		handling{count: true, store: true, partition: partition}); err != nil {
		t.Fatalf("absorbMessage: %v", err)
	}
}

// The whole fix in one place: when a partition moves between two live workers,
// what this worker was holding for it must go, and the records behind it must
// become deliverable again.
//
// Both halves or neither. Dropping the spans while the in-process watermark
// still remembers their offsets would make the same worker skip them if the
// partition came back — and those spans were dropped, not stored, so that is a
// hole rather than the duplicate this exists to stop.
func TestAHandoverReleasesTheBufferAndTheReplayGuardTogether(t *testing.T) {
	pipeline := pipelineForHandover(t, newRecordingStore())

	absorbOn(t, pipeline, spansMoved, 100, 1)
	absorbOn(t, pipeline, spansKept, 100, 2)

	pipeline.release(map[string][]int32{pipeline.topic: {spansMoved}})

	released := pipeline.stages.buffer.Expire(invariantStart.Add(2 * samplingWindow))
	if len(released) != spansPerTrace {
		t.Errorf("Expire() released %d rows, want the %d of the trace on the partition "+
			"this worker still owns; the rest are rows the new owner writes too",
			len(released), spansPerTrace)
	}
	if disowned := pipeline.disownedSpans; disowned != spansPerTrace {
		t.Errorf("disowned %d spans, want %d", disowned, spansPerTrace)
	}

	if !pipeline.seen.FirstDelivery(pipeline.topic, spansMoved, 100) {
		t.Error("the moved partition's record is still remembered as delivered; " +
			"if the partition came back, nothing would ever write those spans")
	}
	if pipeline.seen.FirstDelivery(pipeline.topic, spansKept, 100) {
		t.Error("a partition this worker still owns forgot its replay watermark")
	}
}

// A revoke names some partitions, not all of them. The ones it does not name
// carry on being buffered and released exactly as before.
func TestAHandoverLeavesThePartitionsItDidNotName(t *testing.T) {
	pipeline := pipelineForHandover(t, newRecordingStore())
	absorbOn(t, pipeline, spansKept, 100, 1)

	pipeline.release(map[string][]int32{"logs-raw": {spansKept}})
	pipeline.release(map[string][]int32{pipeline.topic: {}})

	released := pipeline.stages.buffer.Expire(invariantStart.Add(2 * samplingWindow))
	if len(released) != spansPerTrace {
		t.Errorf("Expire() released %d rows, want %d: another topic's rebalance and an "+
			"empty set must both leave this pipeline alone", len(released), spansPerTrace)
	}
}

// Revoked and lost differ in one thing, and it is not the release. A lost
// session means these partitions may have been another worker's for a while, so
// the durable state this process has cached for them can no longer be believed;
// a cooperative revoke leaves the cache correct and re-reads it on the
// assignment that brings the partition back.
func TestALostSessionMarksTheDurableStateForRereading(t *testing.T) {
	store := &countingStore{recordingStore: newRecordingStore()}
	pipeline := pipelineForHandover(t, store)

	if err := pipeline.ingest.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh: %v", err)
	}
	if store.loads != 1 {
		t.Fatalf("the guard read the store %d times at startup, want 1", store.loads)
	}

	pipeline.release(map[string][]int32{pipeline.topic: {spansMoved}})
	if err := pipeline.ingest.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh after a revoke: %v", err)
	}
	if store.loads != 1 {
		t.Errorf("a cooperative revoke re-read the store; the cache is still correct "+
			"and the read costs the rebalance it is blocking (loads = %d)", store.loads)
	}

	pipeline.releaseLost(map[string][]int32{pipeline.topic: {spansMoved}})
	if err := pipeline.ingest.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh after a loss: %v", err)
	}
	if store.loads != 2 {
		t.Errorf("loads = %d, want 2: a lost session must re-read what another "+
			"worker may have settled while we believed we owned these partitions",
			store.loads)
	}
}

// countingStore is recordingStore with a tally of reads, which is how a test
// tells a cache that was invalidated from one that was not.
type countingStore struct {
	*recordingStore
	loads int
}

func (s *countingStore) Load(ctx context.Context) (map[ingeststate.Partition]ingeststate.State, error) {
	s.loads++
	return s.recordingStore.Load(ctx)
}

// A handover must cost no call out of the process. It runs on the client's
// goroutine with the whole group's rebalance waiting on it, and the loop's own
// retry budget is two minutes against a sixty-second rebalance timeout — long
// enough to lose the session and move every other partition this worker owns.
func TestAHandoverMakesNoCallOutOfTheProcess(t *testing.T) {
	pipeline := pipelineForHandover(t, refusingStore{})
	absorbOn(t, pipeline, spansMoved, 100, 1)

	// A store that fails every call: reaching it at all would panic the test.
	pipeline.release(map[string][]int32{pipeline.topic: {spansMoved}})

	if held := pipeline.stages.buffer.Stats().BufferedSpans; held != 0 {
		t.Errorf("the buffer still holds %d spans of the partition that left", held)
	}
}

// refusingStore panics on any use, so a test fails loudly rather than silently
// tolerating a call that must not happen.
type refusingStore struct{}

func (refusingStore) Load(context.Context) (map[ingeststate.Partition]ingeststate.State, error) {
	panic("the rebalance callback read the durable state")
}

func (refusingStore) Save(context.Context, map[ingeststate.Partition]ingeststate.State) error {
	panic("the rebalance callback wrote the counter watermark")
}

func (refusingStore) SaveWritten(context.Context, ingeststate.Written) error {
	panic("the rebalance callback wrote the span watermark")
}

// Progress held after a refusal belongs to the partitions this worker owns. A
// partition that moved is recorded by its new owner, so the hold this worker
// had for it must go with the handover, or its end would write behind the new
// owner's progress (C1).
func TestAHandoverDropsTheProgressHoldOfThePartitionThatLeft(t *testing.T) {
	rows := &progressRows{rows: make(ingeststate.Marks)}
	pipeline := pipelineForHandover(t, newRecordingStore())
	pipeline.progress = ingeststate.NewProgress(rows, ingeststate.MaxProgressHold)
	moved := ingeststate.Partition{Topic: pipeline.topic, ID: spansMoved}
	kept := ingeststate.Partition{Topic: pipeline.topic, ID: spansKept}

	if _, err := pipeline.progress.Record(t.Context(), invariantStart,
		ingeststate.Marks{moved: {CountedThrough: 100}, kept: {CountedThrough: 200}}, true); err != nil {
		t.Fatalf("Record: %v", err)
	}
	pipeline.release(map[string][]int32{pipeline.topic: {spansMoved}})
	if _, err := pipeline.progress.Record(t.Context(),
		invariantStart.Add(ingeststate.MaxProgressHold+time.Second), nil, false); err != nil {
		t.Fatalf("Record: %v", err)
	}

	if _, written := rows.rows[moved]; written {
		t.Errorf("the partition that left had its progress written: %+v", rows.rows[moved])
	}
	if rows.rows[kept].CountedThrough != 200 {
		t.Errorf("the partition still owned recorded %+v, want 200", rows.rows[kept])
	}
}
