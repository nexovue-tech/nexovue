// Package dedupe makes a redelivered Kafka record harmless to the stages that
// aggregate.
//
// Delivery is at-least-once by design: offsets commit only after the write
// returns, so a crash or a rebalance between the two replays the batch. Writing
// the same span row twice changes nothing, but adding the same span to a
// counter twice does — so the counters consume a deduplicated stream.
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
// are counted again. Closing that gap needs durable state, and the shape it
// should take is an open question rather than a silent assumption.
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
