// Package dedupe makes a redelivered Kafka record harmless.
//
// Delivery is at-least-once by design: offsets commit only after the write
// returns, so a crash or a rebalance between the two replays the batch. Adding
// the same span to a counter twice is a wrong number, and writing the same span
// row twice is a row the customer sees twice in their trace — `spans` is a
// plain MergeTree and nothing deduplicates it on read. Both stages therefore
// consume a deduplicated stream.
//
// The identity of a delivery is (topic, partition, offset). A redelivery is
// literally the same offset, which makes the check exact and the state one
// integer per partition rather than one entry per span.
package dedupe

import "sync"

// Offsets remembers the highest offset already handed to the aggregating
// stages, per partition.
//
// It covers redelivery within one process — a retried flush, a rebalance that
// returns a partition, a poll replay. It cannot cover a restart: the watermark
// dies with the process, and the records between the last commit and the crash
// would be replayed with nothing to recognise them.
//
// That gap is closed by internal/pipeline/ingeststate, which keeps the same
// facts durably in Keeper: how far each partition is counted (ADR 0061), how far
// its spans are written (migration 000026), and how far the log and metric
// pipelines have written theirs (migration 000029). This type remains the cheap
// in-process half — a map lookup rather than a cache of a remote table — and for
// spans both are consulted, because either alone leaves a hole.
//
// The log and metric pipelines use the durable watermark alone. They buffer
// nothing, so it advances on every insert and its cache is already the
// in-process answer; a second watermark would only repeat it.
type Offsets struct {
	mu        sync.Mutex
	watermark map[partition]int64
}

type partition struct {
	topic string
	id    int32
}

// NewOffsets returns an empty watermark set.
func NewOffsets() *Offsets {
	return &Offsets{watermark: make(map[partition]int64)}
}

// FirstDelivery reports whether this record has not been seen before, and
// records it when it has not. A record at or below the watermark for its
// partition is a redelivery and returns false.
func (o *Offsets) FirstDelivery(topic string, id int32, offset int64) bool {
	key := partition{topic: topic, id: id}

	o.mu.Lock()
	defer o.mu.Unlock()

	if highest, seen := o.watermark[key]; seen && offset <= highest {
		return false
	}
	o.watermark[key] = offset
	return true
}

// Partitions reports how many partitions are tracked, which is the whole of
// this type's memory cost.
func (o *Offsets) Partitions() int {
	o.mu.Lock()
	defer o.mu.Unlock()
	return len(o.watermark)
}
