package usage

import (
	"context"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dedupe"
)

// retrier runs one call out of the process until it succeeds or the budget runs
// out. The consume loop owns that budget; taking it as a function is what lets
// the counting below be tested without a broker, the way trace assembly keeps
// its stages away from the loop.
type retrier func(ctx context.Context, what string, op func(context.Context) error) error

// counting is the loop-free half of this job: what has been counted, what has
// already been counted before, and the flush that moves both.
//
// Idempotency has two halves, and both are needed. `seen` covers redelivery
// inside this process — a rebalance that returns a partition, a poll replay —
// and dies with it. `counted` is the durable half: it is written in the same
// transaction as the counters, so a restart, a crash between the write and the
// commit, or a partition moving to another worker all resume from a watermark
// that cannot disagree with the numbers in the table.
type counting struct {
	signal Signal
	store  Store
	retry  retrier
	log    *slog.Logger

	meter   *Meter
	seen    *dedupe.Offsets
	counted map[int32]int64
	reached map[int32]int64

	// stale is set from the consumer group's assignment callback, on another
	// goroutine, so it is atomic. A partition can arrive from a worker whose
	// flushes this process never saw.
	stale atomic.Bool

	// flushes is how many transactions have committed. A batch absorbed before
	// one of them has had its counters stored, which is what lets its offsets
	// move.
	flushes uint64

	totals totals
}

// totals are the running numbers the heartbeat reports.
type totals struct {
	counters      Counts
	messages      uint64
	replayed      uint64
	undecodable   uint64
	rowsWritten   uint64
	rowsUnmatched uint64
}

func newCounting(signal Signal, store Store, retry retrier, log *slog.Logger) *counting {
	c := &counting{
		signal:  signal,
		store:   store,
		retry:   retry,
		log:     log,
		meter:   NewMeter(),
		seen:    dedupe.NewOffsets(),
		counted: make(map[int32]int64),
		reached: make(map[int32]int64),
	}
	c.stale.Store(true)
	return c
}

// invalidate marks the durable watermark due a re-read.
func (c *counting) invalidate() { c.stale.Store(true) }

// absorb counts one polled batch into the meter. Nothing is written: the flush
// is on a timer, so a Postgres round trip is never in the path of a batch.
func (c *counting) absorb(ctx context.Context, records []*kgo.Record, at time.Time) error {
	if err := c.refresh(ctx); err != nil {
		return err
	}

	for _, record := range records {
		// Both watermarks have to be consulted and the in-process one updated
		// either way, so neither call may be short-circuited away.
		firstDelivery := c.seen.FirstDelivery(record.Topic, record.Partition, record.Offset)
		c.advanceReached(record.Partition, record.Offset)
		if !firstDelivery || c.alreadyCounted(record.Partition, record.Offset) {
			c.totals.replayed++
			continue
		}

		envelope, counts, err := c.signal.meter(record.Value)
		if err != nil {
			c.reportUndecodable(ctx, record, err)
			continue
		}
		c.meter.Observe(
			envelope.GetTenantId(),
			envelope.GetProjectId(),
			consume.ReceivedAt(envelope, at),
			counts,
		)
		c.totals.counters.add(counts)
		c.totals.messages++
	}
	return nil
}

// flush writes one transaction's worth of counters and the offsets they make
// exact. The store applies both or neither, so replaying these records cannot
// add them a second time.
func (c *counting) flush(ctx context.Context) error {
	offsets := c.unflushedOffsets()
	if len(offsets) == 0 {
		return nil
	}
	rows := c.meter.Drain()

	var applied Applied
	if err := c.retry(ctx, "write usage counters", func(callCtx context.Context) error {
		var err error
		applied, err = c.store.Apply(callCtx, rows, offsets)
		return err
	}); err != nil {
		return err
	}

	c.markCounted(offsets)
	c.flushes++
	c.totals.rowsWritten += uint64(applied.Written)
	c.totals.rowsUnmatched += uint64(applied.Unattributed)

	if applied.AlreadyCounted {
		// The transaction had committed and its acknowledgement was lost, so
		// the retry found the offsets already advanced and added nothing. Said
		// out loud, because it is the case this design exists for.
		c.log.InfoContext(ctx, "usage flush had already been applied; counters left alone",
			slog.Int("tenant_days", len(rows)),
			slog.Int("partitions", len(offsets)),
		)
		return nil
	}
	if applied.Unattributed > 0 {
		// A project that is not in the control plane: deleted, or an id that
		// never was one. Its telemetry is metered nowhere, so it is said.
		c.log.WarnContext(ctx, "usage counters had no matching project",
			slog.Int("tenant_days", applied.Unattributed))
	}
	c.log.DebugContext(ctx, "usage flushed",
		slog.Int("tenant_days", applied.Written),
		slog.Int("partitions", len(offsets)),
	)
	return nil
}

// refresh re-reads the durable watermark when partitions have moved. It must
// succeed before anything is counted: counting on a stale watermark is what
// double-counts.
func (c *counting) refresh(ctx context.Context) error {
	if !c.stale.Load() {
		return nil
	}

	var counted map[int32]int64
	if err := c.retry(ctx, "read counted offsets", func(callCtx context.Context) error {
		var err error
		counted, err = c.store.CountedThrough(callCtx)
		return err
	}); err != nil {
		return err
	}

	// Whole-map replacement is safe: nothing is cached until the store has
	// committed it, so a read returns this worker's own writes or those of the
	// worker that held the partition before it.
	c.counted, c.reached = counted, make(map[int32]int64)
	c.stale.Store(false)
	c.log.InfoContext(ctx, "read the counted offsets for this assignment",
		slog.Int("partitions", len(counted)))
	return nil
}

// alreadyCounted reports whether a record's counters are already in Postgres.
func (c *counting) alreadyCounted(partition int32, offset int64) bool {
	counted, known := c.counted[partition]
	return known && offset <= counted
}

// advanceReached remembers the highest offset absorbed on a partition, counted
// or not. A replayed or unreadable record contributes nothing to the counters,
// but its offset is still accounted for, so the watermark leaves no hole.
func (c *counting) advanceReached(partition int32, offset int64) {
	if highest, seen := c.reached[partition]; seen && offset <= highest {
		return
	}
	c.reached[partition] = offset
}

// unflushedOffsets is what this flush would make exact: the partitions whose
// absorbed offsets are past what the store has recorded.
func (c *counting) unflushedOffsets() map[int32]int64 {
	offsets := make(map[int32]int64, len(c.reached))
	for partition, offset := range c.reached {
		if counted, known := c.counted[partition]; known && offset <= counted {
			continue
		}
		offsets[partition] = offset
	}
	return offsets
}

// markCounted moves the in-process watermark to what the store just accepted.
func (c *counting) markCounted(offsets map[int32]int64) {
	for partition, offset := range offsets {
		c.counted[partition] = offset
	}
}

// reportUndecodable records a message this job cannot read.
//
// It is not parked. The pipeline that stores this topic consumes the same
// message and parks it there with its coordinates, and a parked message
// bypassed masking — a second copy of unmasked customer data on the dead-letter
// topic would add a disclosure surface and no information. What a meter owes is
// that the message is not silently ignored, and this line and the heartbeat's
// counter are that.
func (c *counting) reportUndecodable(ctx context.Context, record *kgo.Record, cause error) {
	c.totals.undecodable++
	c.log.WarnContext(ctx, "could not meter a message; it is parked by the pipeline that stores it",
		slog.String("topic", record.Topic),
		slog.Int("partition", int(record.Partition)),
		slog.Int64("offset", record.Offset),
		slog.String("error", cause.Error()),
	)
}
