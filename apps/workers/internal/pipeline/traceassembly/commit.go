package traceassembly

import (
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
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

	// polledAt is when this batch was taken off the broker. The age of the
	// oldest one held is how far behind ingest the committed offsets are, which
	// is the number an operator otherwise has to reach into Kafka for.
	polledAt time.Time

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

// countedThrough reports, per partition, the offsets held whose counter
// contributions have all been folded into the drain about to be written or an
// earlier one. It is what the durable ingest state records (ADR 0061).
//
// It scans the same prefix `ready` does and stops at the same place, but it
// does not wait for the sampling window: a batch's spans can still be in the
// buffer while its counters are already exact, and only the counters need this.
func (q *commitQueue) countedThrough(
	countersDrained func(minute time.Time) bool,
) map[ingeststate.Partition]ingeststate.Range {
	reached := make(map[ingeststate.Partition]ingeststate.Range)
	for _, entry := range q.entries {
		if !countersDrained(entry.latestMinute) {
			break
		}
		for _, at := range entry.positions {
			key := ingeststate.Partition{Topic: at.topic, ID: at.partition}
			offsets, seen := reached[key]
			if !seen {
				reached[key] = ingeststate.Range{Min: at.offset, Max: at.offset}
				continue
			}
			reached[key] = ingeststate.Range{
				Min: min(offsets.Min, at.offset),
				Max: max(offsets.Max, at.offset),
			}
		}
	}
	return reached
}

// incomingRanges is the offset range each partition contributes to one polled
// batch, which is the "new batch" the state machine classifies.
func incomingRanges(records []*kgo.Record) map[ingeststate.Partition]ingeststate.Range {
	ranges := make(map[ingeststate.Partition]ingeststate.Range)
	for _, record := range records {
		key := ingeststate.Partition{Topic: record.Topic, ID: record.Partition}
		offsets, seen := ranges[key]
		if !seen {
			ranges[key] = ingeststate.Range{Min: record.Offset, Max: record.Offset}
			continue
		}
		ranges[key] = ingeststate.Range{
			Min: min(offsets.Min, record.Offset),
			Max: max(offsets.Max, record.Offset),
		}
	}
	return ranges
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

// commitLag is how long the oldest batch still held has been waiting. Batches
// are held in poll order, so the front one is the oldest. Nothing held is no
// lag.
//
// About 70s is the designed steady state — a minute bucket, its grace and the
// sampling window. A number that keeps climbing past that is a pipeline that
// has stopped settling.
func (q *commitQueue) commitLag(now time.Time) time.Duration {
	if len(q.entries) == 0 {
		return 0
	}
	oldest := q.entries[0].polledAt
	if oldest.IsZero() || now.Before(oldest) {
		return 0
	}
	return now.Sub(oldest)
}

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
