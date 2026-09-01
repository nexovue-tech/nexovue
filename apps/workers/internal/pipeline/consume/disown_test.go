package consume

import (
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

// positionsOn is one batch's coordinates across the given partitions, one
// record each, at ascending offsets.
func positionsOn(partitions ...int32) []Position {
	positions := make([]Position, 0, len(partitions))
	for offset, partition := range partitions {
		positions = append(positions, Position{
			Topic: spansTopic, Partition: partition, Offset: int64(offset),
		})
	}
	return positions
}

// A revoked partition's offsets must never be committed. The stages release
// what they were holding for it at the same instant, so a commit would claim
// work nothing finished — and a hole cannot be repaired where a duplicate row
// can.
func TestForgettingAPartitionDropsOnlyItsOffsets(t *testing.T) {
	var queue Queue[bool]
	queue.Hold(Batch[bool]{Positions: positionsOn(0, 1, 2), PolledAt: started})
	queue.Hold(Batch[bool]{Positions: positionsOn(1, 1), PolledAt: started})

	if dropped := queue.Forget(map[string][]int32{spansTopic: {1}}); dropped != 3 {
		t.Fatalf("Forget() dropped %d offsets, want 3", dropped)
	}
	if queue.Records() != 2 {
		t.Errorf("Records() = %d, want the two offsets on partitions we still own",
			queue.Records())
	}

	due := queue.Ready(func(bool) bool { return true })
	for _, at := range due {
		if at.Partition == 1 {
			t.Errorf("committed offset %d on a revoked partition", at.Offset)
		}
	}
	if len(due) != 2 {
		t.Errorf("Ready() = %d offsets, want 2", len(due))
	}
}

// A batch that held nothing but the revoked partition goes with it. Left in
// place at the front of the queue it would report a commit lag it is not
// causing, which is the number an operator reads to find a wedged pipeline.
func TestABatchOfOnlyRevokedOffsetsStopsHoldingTheLagBack(t *testing.T) {
	var queue Queue[bool]
	queue.Hold(Batch[bool]{Positions: positionsOn(1, 1), PolledAt: started})
	queue.Hold(Batch[bool]{Positions: positionsOn(0), PolledAt: started.Add(time.Minute)})

	queue.Forget(map[string][]int32{spansTopic: {1}})

	if lag := queue.Lag(started.Add(2 * time.Minute)); lag != time.Minute {
		t.Errorf("Lag() = %s, want 1m measured from the oldest batch still holding "+
			"something; the emptied one is not why commits are behind", lag)
	}
}

// Records polled but never absorbed produced nothing, so nothing has to be
// undone — but absorbing them after the handover would write rows the new owner
// writes as well.
func TestForgettingAPartitionDropsItsUnabsorbedRecords(t *testing.T) {
	var batch pendingBatch
	for _, partition := range []int32{0, 1, 1} {
		batch.add(&kgo.Record{Topic: spansTopic, Partition: partition, Value: []byte("envelope")})
	}
	batch.openAt(started, 2*time.Second)

	if dropped := batch.forget(map[string][]int32{spansTopic: {1}}); dropped != 2 {
		t.Fatalf("forget() dropped %d records, want 2", dropped)
	}
	if len(batch.records) != 1 || batch.records[0].Partition != 0 {
		t.Fatalf("the batch holds %d records, want only partition 0's", len(batch.records))
	}
	if batch.bytes != len("envelope") {
		t.Errorf("bytes = %d, want the one surviving record's size; a byte bound "+
			"counting records that are gone flushes early forever", batch.bytes)
	}
	if batch.flushBy != started.Add(2*time.Second) {
		t.Error("the flush deadline moved while the batch still held a record")
	}
}

// An emptied batch has no deadline to keep: leaving the old one set would make
// the next record it takes look overdue the moment it arrives.
func TestAnEmptiedBatchForgetsItsFlushDeadline(t *testing.T) {
	var batch pendingBatch
	batch.add(&kgo.Record{Topic: spansTopic, Partition: 1})
	batch.openAt(started, 2*time.Second)

	batch.forget(map[string][]int32{spansTopic: {1}})

	if !batch.flushBy.IsZero() {
		t.Errorf("flushBy = %s, want zero once the batch is empty", batch.flushBy)
	}
	if batch.isReady(started, Settings{MaxMessages: 5000, MaxDelay: 2 * time.Second}) {
		t.Error("an empty batch reported itself ready to absorb")
	}
}
