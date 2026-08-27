package traceassembly

import (
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

const spansTopic = "spans-raw"

func offsetsOn(id int32, offsets ...int64) []position {
	positions := make([]position, 0, len(offsets))
	for _, offset := range offsets {
		positions = append(positions, position{topic: spansTopic, partition: id, offset: offset})
	}
	return positions
}

func partitionOf(id int32) ingeststate.Partition {
	return ingeststate.Partition{Topic: spansTopic, ID: id}
}

// The durable state may only claim offsets whose counters are all written. The
// scan therefore stops where `ready` stops, at the first batch whose minute is
// still open — a later batch's offsets are not exact just because an earlier
// one's are.
func TestCountedThroughStopsAtTheFirstUndrainedBatch(t *testing.T) {
	open := time.Date(2026, 8, 27, 12, 5, 0, 0, time.UTC)
	closed := open.Add(-time.Minute)

	var queue commitQueue
	queue.hold(pendingCommit{positions: offsetsOn(0, 10, 11), latestMinute: closed})
	queue.hold(pendingCommit{positions: offsetsOn(0, 12), latestMinute: closed})
	queue.hold(pendingCommit{positions: offsetsOn(0, 13), latestMinute: open})
	queue.hold(pendingCommit{positions: offsetsOn(0, 14), latestMinute: closed})

	reached := queue.countedThrough(func(minute time.Time) bool { return minute.Equal(closed) })

	want := ingeststate.Range{Min: 10, Max: 12}
	if got := reached[partitionOf(0)]; got != want {
		t.Errorf("countedThrough = %+v, want %+v", got, want)
	}
}

func TestCountedThroughKeepsPartitionsApart(t *testing.T) {
	var queue commitQueue
	queue.hold(pendingCommit{positions: offsetsOn(0, 100, 101)})
	queue.hold(pendingCommit{positions: offsetsOn(1, 7)})
	queue.hold(pendingCommit{positions: offsetsOn(0, 102)})

	reached := queue.countedThrough(func(time.Time) bool { return true })

	cases := map[int32]ingeststate.Range{
		0: {Min: 100, Max: 102},
		1: {Min: 7, Max: 7},
	}
	for id, want := range cases {
		if got := reached[partitionOf(id)]; got != want {
			t.Errorf("partition %d = %+v, want %+v", id, got, want)
		}
	}
	if len(reached) != len(cases) {
		t.Errorf("countedThrough covered %d partitions, want %d", len(reached), len(cases))
	}
}

func TestCountedThroughIsEmptyWhenNothingIsDrained(t *testing.T) {
	var queue commitQueue
	queue.hold(pendingCommit{positions: offsetsOn(0, 1, 2)})

	if reached := queue.countedThrough(func(time.Time) bool { return false }); len(reached) != 0 {
		t.Errorf("countedThrough = %+v, want nothing recorded", reached)
	}
}

func TestIncomingRangesBoundEachPartitionInABatch(t *testing.T) {
	records := []*kgo.Record{
		{Topic: spansTopic, Partition: 0, Offset: 5},
		{Topic: spansTopic, Partition: 1, Offset: 90},
		{Topic: spansTopic, Partition: 0, Offset: 3},
		{Topic: spansTopic, Partition: 0, Offset: 9},
	}

	ranges := incomingRanges(records)

	cases := map[int32]ingeststate.Range{
		0: {Min: 3, Max: 9},
		1: {Min: 90, Max: 90},
	}
	for id, want := range cases {
		if got := ranges[partitionOf(id)]; got != want {
			t.Errorf("partition %d = %+v, want %+v", id, got, want)
		}
	}
}
