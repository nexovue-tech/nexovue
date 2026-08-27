package traceassembly

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/ClickHouse/ch-go/chpool"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
)

// Pipeline consumes span envelopes and writes them to ClickHouse.
//
// Offsets are committed only after the insert returns, which makes delivery
// at-least-once: a crash between the insert and the commit replays the batch.
// Re-inserting an identical span row is harmless while nothing downstream
// aggregates on ingest, which is what makes that trade safe in M1.
type Pipeline struct {
	consumer *kgo.Client
	writer   *spanWriter
	block    *spanBlock
	log      *slog.Logger
	limits   config.SpanIngest
}

// New builds the pipeline and joins the consumer group. The caller owns the
// pool; Close releases only what New created.
func New(cfg config.Config, pool *chpool.Pool, log *slog.Logger) (*Pipeline, error) {
	consumer, err := kgo.NewClient(
		kgo.SeedBrokers(cfg.KafkaBrokers...),
		kgo.ConsumeTopics(cfg.Spans.Topic),
		kgo.ConsumerGroup(cfg.Spans.ConsumerGroup),
		// Marks are what MarkCommitRecords records; without this option both
		// it and CommitMarkedOffsets are silent no-ops. Autocommit can then
		// only ever commit offsets we have already inserted.
		kgo.AutoCommitMarks(),
		// A rebalance between polling and committing would let us commit
		// offsets for partitions we no longer own. Blocking it is bounded by
		// the flush deadline, which is far inside the rebalance timeout.
		kgo.BlockRebalanceOnPoll(),
	)
	if err != nil {
		return nil, fmt.Errorf("span consumer: %w", err)
	}
	return &Pipeline{
		consumer: consumer,
		writer:   newSpanWriter(pool),
		block:    newSpanBlock(),
		log:      log.With(slog.String("pipeline", string(config.PipelineTraceAssembly))),
		limits:   cfg.Spans,
	}, nil
}

// Close leaves the consumer group and releases the client.
func (p *Pipeline) Close() { p.consumer.Close() }

// Run consumes until ctx is cancelled, flushing a batch when it reaches the row
// limit or when the oldest row in it reaches the delay limit, whichever is
// first. The delay limit is what bounds ingest lag while a tenant is quiet
// enough that the row limit alone would never be reached.
func (p *Pipeline) Run(ctx context.Context) error {
	p.log.InfoContext(ctx, "trace assembly started",
		slog.String("topic", p.limits.Topic),
		slog.String("group", p.limits.ConsumerGroup),
		slog.Int("batch_max_rows", p.limits.MaxRows),
		slog.Duration("batch_max_delay", p.limits.MaxDelay),
	)
	defer p.log.InfoContext(ctx, "trace assembly stopped")

	var pending []*kgo.Record
	var flushBy time.Time

	for {
		if ctx.Err() != nil {
			return nil
		}

		fetches, err := p.poll(ctx, len(pending), flushBy)
		if errors.Is(err, errClientClosed) {
			return nil
		}
		if err != nil {
			return err
		}
		fetches.EachRecord(func(record *kgo.Record) { pending = append(pending, record) })
		if len(pending) > 0 && flushBy.IsZero() {
			flushBy = time.Now().Add(p.limits.MaxDelay)
		}

		if len(pending) == 0 || (len(pending) < p.limits.MaxRows && time.Now().Before(flushBy)) {
			continue
		}
		if err := p.flush(ctx, pending); err != nil {
			return err
		}
		pending, flushBy = pending[:0], time.Time{}
	}
}

// poll waits for records, giving up at the flush deadline once a batch has
// started. Records already fetched stay buffered in the client, so a poll cut
// short by the deadline loses nothing.
func (p *Pipeline) poll(
	ctx context.Context,
	pending int,
	flushBy time.Time,
) (kgo.Fetches, error) {
	pollCtx, cancel := context.WithCancel(ctx)
	if pending > 0 {
		cancel()
		pollCtx, cancel = context.WithDeadline(ctx, flushBy)
	}
	defer cancel()

	fetches := p.consumer.PollRecords(pollCtx, p.limits.MaxRows-pending)
	if fetches.IsClientClosed() {
		return fetches, errClientClosed
	}
	return fetches, p.reportFetchErrors(ctx, fetches)
}

// errClientClosed ends Run when the consumer is closed under it, which happens
// during shutdown and is not a failure.
var errClientClosed = errors.New("kafka consumer closed")

// reportFetchErrors logs what franz-go will retry on its own and returns only
// what Run cannot continue past. A deadline of our own making is the flush
// timer firing, not a fault.
func (p *Pipeline) reportFetchErrors(ctx context.Context, fetches kgo.Fetches) error {
	for _, fetchErr := range fetches.Errors() {
		switch {
		case errors.Is(fetchErr.Err, context.DeadlineExceeded),
			errors.Is(fetchErr.Err, context.Canceled):
			continue
		default:
			p.log.WarnContext(ctx, "fetch failed, retrying",
				slog.String("topic", fetchErr.Topic),
				slog.Int("partition", int(fetchErr.Partition)),
				slog.String("error", fetchErr.Err.Error()),
			)
		}
	}
	return nil
}

// flush writes every record's rows, then marks and commits the offsets. The
// order is the durability guarantee: nothing is committed that is not stored.
func (p *Pipeline) flush(ctx context.Context, records []*kgo.Record) error {
	p.block.reset()
	decoded := 0
	for _, record := range records {
		rows, err := flattenEnvelope(record.Value)
		if err != nil {
			// One malformed message must not stall the partition behind it.
			// It is dropped with its coordinates logged; the dead-letter topic
			// that would keep it arrives with P1-16.
			p.log.WarnContext(ctx, "dropping undecodable span message",
				slog.String("topic", record.Topic),
				slog.Int("partition", int(record.Partition)),
				slog.Int64("offset", record.Offset),
				slog.String("error", err.Error()),
			)
			continue
		}
		for _, row := range rows {
			p.block.append(row)
		}
		decoded++
	}

	if err := p.writer.insert(ctx, p.block); err != nil {
		return err
	}

	p.consumer.MarkCommitRecords(records...)
	if err := p.consumer.CommitMarkedOffsets(ctx); err != nil {
		return fmt.Errorf("commit offsets: %w", err)
	}
	// Held since the first poll of this batch, so the commit above could not
	// race a reassignment.
	p.consumer.AllowRebalance()

	p.log.DebugContext(ctx, "spans written",
		slog.Int("messages", decoded),
		slog.Int("rows", p.block.rows()),
	)
	return nil
}
