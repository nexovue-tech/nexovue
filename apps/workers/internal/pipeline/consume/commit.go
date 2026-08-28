package consume

import (
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

// Position is where one record sat on the broker — everything needed to commit
// it and nothing else.
//
// The record itself is deliberately not kept: offsets are held for as long as
// it takes a batch's output to reach storage, and holding the payloads for that
// long would pin a minute of ingest in memory.
type Position struct {
	Topic       string
	Partition   int32
	Offset      int64
	LeaderEpoch int32
}

// PositionOf reads one record's coordinates.
func PositionOf(record *kgo.Record) Position {
	return Position{
		Topic:       record.Topic,
		Partition:   record.Partition,
		Offset:      record.Offset,
		LeaderEpoch: record.LeaderEpoch,
	}
}

// PositionsOf reads a whole batch's coordinates, in the order they were polled.
func PositionsOf(records []*kgo.Record) []Position {
	positions := make([]Position, 0, len(records))
	for _, record := range records {
		positions = append(positions, PositionOf(record))
	}
	return positions
}

// Batch is one polled batch, held until everything it produced has been stored.
//
// Produced is whatever the pipeline's stages have to remember in order to
// answer that question later — a deadline, the buckets the batch fed, or
// nothing at all for a pipeline that writes its records where it decodes them.
type Batch[S any] struct {
	Positions []Position

	// PolledAt is when this batch was taken off the broker. The age of the
	// oldest one held is how far behind ingest the committed offsets are, which
	// is the number an operator otherwise has to reach into Kafka for.
	PolledAt time.Time

	Produced S
}

// Queue holds batches in the order they were polled. Order matters: a Kafka
// commit moves a partition's offset to a point, so committing a later batch
// would silently declare an earlier one done.
type Queue[S any] struct {
	entries []Batch[S]
	count   int
}

// Hold takes a batch's offsets into the queue. Nothing commits them until
// stored reports that what the batch produced has reached storage.
func (q *Queue[S]) Hold(entry Batch[S]) {
	q.entries = append(q.entries, entry)
	q.count += len(entry.Positions)
}

// Ready pops every batch from the front that is fully stored, stopping at the
// first that is not.
func (q *Queue[S]) Ready(stored func(S) bool) []Position {
	due, settled := q.prefix(stored)
	q.entries = q.entries[settled:]
	q.count -= len(due)
	return due
}

// Reached reports the offsets held whose output is stored, without releasing
// them.
//
// It scans the same prefix Ready does and stops at the same place. A pipeline
// whose batches produce several things asks with one of them: a batch's rows
// can still be buffered while its counters are already exact, and a durable
// record of the counted offsets needs to be written before the offsets move
// (ADR 0061).
func (q *Queue[S]) Reached(stored func(S) bool) []Position {
	reached, _ := q.prefix(stored)
	return reached
}

// prefix scans from the front while stored holds, returning those positions and
// how many batches they came from.
func (q *Queue[S]) prefix(stored func(S) bool) ([]Position, int) {
	var positions []Position
	settled := 0
	for _, entry := range q.entries {
		if !stored(entry.Produced) {
			break
		}
		positions = append(positions, entry.Positions...)
		settled++
	}
	return positions, settled
}

// Drain releases every held batch. Shutdown uses it, after the stages have
// themselves been drained to storage.
func (q *Queue[S]) Drain() []Position {
	var all []Position
	for _, entry := range q.entries {
		all = append(all, entry.Positions...)
	}
	q.entries, q.count = nil, 0
	return all
}

// Records reports how many records are waiting, which is the replay a crash
// would cost and the number a lag dashboard would show.
func (q *Queue[S]) Records() int { return q.count }

// Lag is how long the oldest batch still held has been waiting. Batches are
// held in poll order, so the front one is the oldest. Nothing held is no lag.
//
// A steady state is whatever the pipeline's own settling takes — for trace
// assembly, about 70s of minute bucket, grace and sampling window. A number
// that keeps climbing past that is a pipeline that has stopped settling.
func (q *Queue[S]) Lag(now time.Time) time.Duration {
	if len(q.entries) == 0 {
		return 0
	}
	oldest := q.entries[0].PolledAt
	if oldest.IsZero() || now.Before(oldest) {
		return 0
	}
	return now.Sub(oldest)
}

// Markable turns positions into the map franz-go marks, keeping the highest
// offset per partition. Kafka commits the *next* offset to read, which is why
// each one is advanced by one.
func Markable(positions []Position) map[string]map[int32]kgo.EpochOffset {
	if len(positions) == 0 {
		return nil
	}
	marks := make(map[string]map[int32]kgo.EpochOffset)
	for _, at := range positions {
		partitions, ok := marks[at.Topic]
		if !ok {
			partitions = make(map[int32]kgo.EpochOffset)
			marks[at.Topic] = partitions
		}
		next := kgo.EpochOffset{Epoch: at.LeaderEpoch, Offset: at.Offset + 1}
		if current, seen := partitions[at.Partition]; !seen || current.Less(next) {
			partitions[at.Partition] = next
		}
	}
	return marks
}
