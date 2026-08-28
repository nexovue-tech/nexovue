package consume

import (
	"testing"
	"time"
)

const spansTopic = "spans-raw"

// held is one batch of offsets on one partition, stored under the marker the
// tests use as "what this batch still owes storage".
func held(partition int32, stored bool, offsets ...int64) Batch[bool] {
	positions := make([]Position, 0, len(offsets))
	for _, offset := range offsets {
		positions = append(positions, Position{
			Topic: spansTopic, Partition: partition, Offset: offset,
		})
	}
	return Batch[bool]{Positions: positions, Produced: stored}
}

func isStored(stored bool) bool { return stored }

func offsetsOf(positions []Position) []int64 {
	offsets := make([]int64, 0, len(positions))
	for _, at := range positions {
		offsets = append(offsets, at.Offset)
	}
	return offsets
}

func sameOffsets(t *testing.T, got []Position, want ...int64) {
	t.Helper()
	offsets := offsetsOf(got)
	if len(offsets) != len(want) {
		t.Fatalf("offsets = %v, want %v", offsets, want)
	}
	for index, offset := range offsets {
		if offset != want[index] {
			t.Fatalf("offsets = %v, want %v", offsets, want)
		}
	}
}

// A caller may only claim offsets whose output is stored. The scan therefore
// stops where Ready stops, at the first batch that is not — a later batch is
// not stored just because an earlier one is.
func TestReachedStopsAtTheFirstBatchThatIsNotStored(t *testing.T) {
	var queue Queue[bool]
	queue.Hold(held(0, true, 10, 11))
	queue.Hold(held(0, true, 12))
	queue.Hold(held(0, false, 13))
	queue.Hold(held(0, true, 14))

	sameOffsets(t, queue.Reached(isStored), 10, 11, 12)
}

func TestReachedIsEmptyWhenNothingIsStored(t *testing.T) {
	var queue Queue[bool]
	queue.Hold(held(0, false, 1, 2))

	if reached := queue.Reached(isStored); len(reached) != 0 {
		t.Errorf("Reached = %v, want nothing reported", offsetsOf(reached))
	}
}

// Reached answers about one of the things a batch produced, while the batch as
// a whole may still owe the store something else. Releasing there would commit
// past what is stored.
func TestReachedDoesNotReleaseWhatItReports(t *testing.T) {
	var queue Queue[bool]
	queue.Hold(held(0, true, 10, 11))

	queue.Reached(isStored)

	if queue.Records() != 2 {
		t.Errorf("Records() = %d after Reached, want the batch still held", queue.Records())
	}
	sameOffsets(t, queue.Ready(isStored), 10, 11)
	if queue.Records() != 0 {
		t.Errorf("Records() = %d after Ready, want the batch released", queue.Records())
	}
}

// Kafka commits move a partition's offset to a point, so releasing a later
// batch before an earlier one would declare the earlier one done.
func TestReadyReleasesInOrder(t *testing.T) {
	var queue Queue[bool]
	queue.Hold(held(0, false, 1))
	queue.Hold(held(0, true, 2))

	if due := queue.Ready(isStored); len(due) != 0 {
		t.Errorf("Ready released %d records past a batch that is not stored yet", len(due))
	}
	queue.entries[0].Produced = true
	sameOffsets(t, queue.Ready(isStored), 1, 2)
}

// Kafka commits the next offset to read, and one partition may appear in many
// batches; marking the wrong one either replays or skips.
func TestMarkableKeepsTheHighestOffsetPerPartition(t *testing.T) {
	marks := Markable([]Position{
		{Topic: spansTopic, Partition: 0, Offset: 10},
		{Topic: spansTopic, Partition: 0, Offset: 42},
		{Topic: spansTopic, Partition: 0, Offset: 7},
		{Topic: spansTopic, Partition: 1, Offset: 3},
	})

	if got := marks[spansTopic][0].Offset; got != 43 {
		t.Errorf("partition 0 marked at %d, want the highest offset plus one (43)", got)
	}
	if got := marks[spansTopic][1].Offset; got != 4 {
		t.Errorf("partition 1 marked at %d, want 4", got)
	}
	if Markable(nil) != nil {
		t.Error("Markable(nil) returned a map; nothing to commit must commit nothing")
	}
}

// Nothing held is not lag, and a zero poll time is not a fifty-six-year-old
// batch.
func TestNothingHeldIsNoCommitLag(t *testing.T) {
	now := time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)

	var queue Queue[bool]
	if lag := queue.Lag(now); lag != 0 {
		t.Errorf("Lag() = %s on an empty queue, want 0", lag)
	}

	queue.Hold(held(0, false, 1))
	if lag := queue.Lag(now); lag != 0 {
		t.Errorf("Lag() = %s for an unstamped batch, want 0", lag)
	}
}
