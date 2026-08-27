package traceassembly

import (
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

// position is where one record sat on the broker — everything needed to commit
// it and nothing else.
//
// The record itself is deliberately not kept: offsets are held for as long as
// it takes the sampler and the counters to store what the batch produced, and
// holding the payloads for that long would pin a minute of ingest in memory.
type position struct {
	topic       string
	partition   int32
	offset      int64
	leaderEpoch int32
}

func positionOf(record *kgo.Record) position {
	return position{
		topic:       record.Topic,
		partition:   record.Partition,
		offset:      record.Offset,
		leaderEpoch: record.LeaderEpoch,
	}
}

// pendingCommit is one polled batch, held until everything it produced has been
// stored.
//
// Two things have to happen before its offsets may move. Its spans have to
// leave the sampler: every trace a record touched opened at or before the
// record was processed, so all of them are decided by then plus the sampling
// window. And its counters have to be written: a batch's rows are only safe
// once every minute bucket it fed has been drained, which is what latestMinute
// is compared against.
type pendingCommit struct {
	positions []position

	// spansStoredAt is when the sampling window over this batch has certainly
	// closed. Before it, some of these spans may still be in the buffer.
	spansStoredAt time.Time

	// latestMinute is the newest counter bucket this batch fed.
	latestMinute time.Time
}

// commitQueue holds batches in the order they were polled. Order matters: a
// Kafka commit moves a partition's offset to a point, so committing a later
// batch would silently declare an earlier one done.
type commitQueue struct {
	entries []pendingCommit
	count   int
}

func (q *commitQueue) hold(entry pendingCommit) {
	q.entries = append(q.entries, entry)
	q.count += len(entry.positions)
}

// ready pops every batch from the front that is fully stored, stopping at the
// first that is not.
func (q *commitQueue) ready(
	now time.Time,
	countersDrained func(minute time.Time) bool,
) []position {
	var due []position
	settled := 0
	for _, entry := range q.entries {
		if now.Before(entry.spansStoredAt) || !countersDrained(entry.latestMinute) {
			break
		}
		due = append(due, entry.positions...)
		settled++
	}
	q.entries = q.entries[settled:]
	q.count -= len(due)
	return due
}

// drain releases every held batch. Shutdown uses it, after the buffer and the
// counters have themselves been drained to storage.
func (q *commitQueue) drain() []position {
	var all []position
	for _, entry := range q.entries {
		all = append(all, entry.positions...)
	}
	q.entries, q.count = nil, 0
	return all
}

// held reports how many records are waiting, which is the replay a crash would
// cost and the number a lag dashboard would show.
func (q *commitQueue) held() int { return q.count }

// markable turns positions into the map franz-go marks, keeping the highest
// offset per partition. Kafka commits the *next* offset to read, which is why
// each one is advanced by one.
func markable(positions []position) map[string]map[int32]kgo.EpochOffset {
	if len(positions) == 0 {
		return nil
	}
	marks := make(map[string]map[int32]kgo.EpochOffset)
	for _, at := range positions {
		partitions, ok := marks[at.topic]
		if !ok {
			partitions = make(map[int32]kgo.EpochOffset)
			marks[at.topic] = partitions
		}
		next := kgo.EpochOffset{Epoch: at.leaderEpoch, Offset: at.offset + 1}
		if current, seen := partitions[at.partition]; !seen || current.Less(next) {
			partitions[at.partition] = next
		}
	}
	return marks
}
