package traceassembly

import (
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

// receivedMessage is traceMessage as the receiver stamped it at a given instant.
func receivedMessage(t *testing.T, index int, receivedAt time.Time) []byte {
	t.Helper()
	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(traceMessage(t, index, false), &envelope); err != nil {
		t.Fatalf("unmarshalling the envelope: %v", err)
	}
	envelope.ReceivedAtUnixNano = receivedAt.UnixNano()
	message, err := proto.Marshal(&envelope)
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	return message
}

// arrival is one record: where it sat on the broker and when it was received.
type arrival struct {
	partition int32
	offset    int64
	after     time.Duration
}

// holdBatch absorbs records through the stages and holds them as one batch, the
// way Absorb does.
func holdBatch(t *testing.T, queue *consume.Queue[spanStorage], stages *stages, records ...arrival) spanStorage {
	t.Helper()
	var (
		produced  spanStorage
		positions []consume.Position
	)
	for index, record := range records {
		result, err := stages.absorbMessage(invariantStart,
			receivedMessage(t, index, invariantStart.Add(record.after)),
			handling{count: true, store: true, partition: record.partition})
		if err != nil {
			t.Fatalf("absorbMessage: %v", err)
		}
		produced.note(ingeststate.Partition{Topic: spansPartition.Topic, ID: record.partition}, result)
		positions = append(positions, consume.Position{
			Topic: spansPartition.Topic, Partition: record.partition, Offset: record.offset,
		})
	}
	queue.Hold(consume.Batch[spanStorage]{Positions: positions, Produced: produced})
	return produced
}

// The scheduler settles a minute once every partition has delivered past it,
// and it knows that only from when each partition's last counted record was
// received (C3). That time has to reach the settlement's progress for exactly
// the offsets the settlement counted — not a record still in an open minute.
func TestTheSettlementCarriesEachPartitionsLastReceivedAt(t *testing.T) {
	pipeline := Pipeline{stages: stagesFor(t, 100)}
	guard := ingeststate.NewGuard(newRecordingStore())
	if err := guard.Refresh(t.Context()); err != nil {
		t.Fatalf("Refresh: %v", err)
	}

	var queue consume.Queue[spanStorage]
	holdBatch(t, &queue, pipeline.stages,
		arrival{partition: 0, offset: 0, after: time.Second},
		arrival{partition: 1, offset: 0, after: 2 * time.Second},
		arrival{partition: 0, offset: 1, after: 3 * time.Second})
	holdBatch(t, &queue, pipeline.stages, arrival{partition: 1, offset: 1, after: 4 * time.Second})
	// A batch whose minute is still open: the settlement must not claim it.
	queue.Hold(consume.Batch[spanStorage]{
		Positions: []consume.Position{{Topic: spansPartition.Topic, Partition: 0, Offset: 2}},
		Produced: spanStorage{
			latestMinute: invariantStart.Add(10 * time.Minute),
			receivedAt: map[ingeststate.Partition]time.Time{
				{Topic: spansPartition.Topic, ID: 0}: invariantStart.Add(9 * time.Minute),
			},
		},
	})

	drained := pipeline.derivedDrained(invariantStart.Add(2 * time.Minute))
	settlement := guard.Advance(ingeststate.RangesOf(queue.Reached(drained)))
	marks := progressMarks(settlement, receivedThrough(queue.ReachedOutput(drained)))

	want := ingeststate.Marks{
		{Topic: spansPartition.Topic, ID: 0}: {CountedThrough: 1, ReceivedAt: invariantStart.Add(3 * time.Second)},
		{Topic: spansPartition.Topic, ID: 1}: {CountedThrough: 1, ReceivedAt: invariantStart.Add(4 * time.Second)},
	}
	if len(marks) != len(want) {
		t.Fatalf("marks = %+v, want %+v", marks, want)
	}
	for partition, mark := range want {
		if got := marks[partition]; got.CountedThrough != mark.CountedThrough || !got.ReceivedAt.Equal(mark.ReceivedAt) {
			t.Errorf("partition %d marked %+v, want %+v", partition.ID, got, mark)
		}
	}
}

// A partition the settlement did not move has nothing new to say, and no
// mark: the scheduler keeps reading its last one.
func TestAPartitionOutsideTheSettlementGetsNoMark(t *testing.T) {
	settled := ingeststate.Partition{Topic: spansPartition.Topic, ID: 0}
	quiet := ingeststate.Partition{Topic: spansPartition.Topic, ID: 1}
	marks := progressMarks(
		ingeststate.Settlement{settled: {Min: 4, Max: 9}},
		map[ingeststate.Partition]time.Time{settled: invariantStart, quiet: invariantStart},
	)
	if _, marked := marks[quiet]; marked || len(marks) != 1 {
		t.Errorf("marks = %+v, want only the settled partition", marks)
	}
}

// A refusal holds every partition with a batch still held, not only the settled
// prefix's: the drained minute's rows can come from a batch that still waits on
// edges, and its partition must not move past the hole once it reaches.
func TestARefusalNamesEveryPartitionWithAHeldBatch(t *testing.T) {
	stages := stagesFor(t, 100)
	var queue consume.Queue[spanStorage]
	holdBatch(t, &queue, stages, arrival{partition: 2, offset: 7})
	holdBatch(t, &queue, stages, arrival{partition: 1, offset: 3})
	settledFirstOnly := func(produced spanStorage) bool {
		_, first := produced.receivedAt[ingeststate.Partition{Topic: spansPartition.Topic, ID: 2}]
		return first
	}

	held := partitionsOf(queue.Reached(everyBatch))

	if prefix := partitionsOf(queue.Reached(settledFirstOnly)); len(prefix) != 1 {
		t.Fatalf("the settled prefix names %v, want only partition 2", prefix)
	}
	want := []ingeststate.Partition{
		{Topic: spansPartition.Topic, ID: 1},
		{Topic: spansPartition.Topic, ID: 2},
	}
	if len(held) != len(want) || held[0] != want[0] || held[1] != want[1] {
		t.Errorf("a refusal holds %v, want %v", held, want)
	}
}
