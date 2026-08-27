package traceassembly

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/ClickHouse/ch-go/chpool"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dedupe"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// drainTimeout bounds the final write on shutdown. It is short because the work
// is one insert of what is already in memory, and because a worker that will
// not exit gets killed instead of drained.
const drainTimeout = 15 * time.Second

// Options are the collaborators the pipeline does not own.
//
// Both are optional. A worker with no control-plane connection masks with the
// compiled-in defaults and samples with the deployment default policy, which is
// how the stages behave for a project that has configured neither.
type Options struct {
	Masking  mask.RulesSource
	Sampling sample.PolicySource
}

// Pipeline consumes span envelopes and runs them through the stages:
// decode → mask → derive → sample → sink.
//
// Two orderings in that list are invariants rather than preferences. `mask`
// comes first because an unmasked value must never reach a counter, a buffer or
// the store. `derive` comes before `sample` because counters are computed from
// every trace, which is what keeps them exact while storage keeps a tenth of
// the healthy ones (ADR 0010).
//
// Offsets commit only after everything a batch produced has been stored —
// spans out of the sampling buffer, counters out of their minute bucket — which
// makes delivery at-least-once. Re-inserting an identical span row is harmless;
// re-counting one is not, which is what the dedupe stage is for.
type Pipeline struct {
	consumer *kgo.Client
	producer *kgo.Client

	writer *spanWriter
	block  *spanBlock

	stages  *stages
	metrics *derive.Writer
	parked  *dlq.Queue
	seen    *dedupe.Offsets
	commits commitQueue

	// running is what readiness reports. A worker whose pipeline has stopped is
	// not ready, however healthy its dependencies look — the failure that taught
	// us this presented as a green container consuming nothing for nine minutes.
	running atomic.Bool

	log      *slog.Logger
	limits   config.SpanIngest
	sampling config.Sampling
	now      func() time.Time // replaced in tests
}

// New builds the pipeline and joins the consumer group. The caller owns the
// ClickHouse pool; Close releases only what New created.
func New(cfg config.Config, pool *chpool.Pool, log *slog.Logger, opts Options) (*Pipeline, error) {
	stages, err := newStages(cfg, opts)
	if err != nil {
		return nil, err
	}

	consumer, err := kgo.NewClient(
		kgo.SeedBrokers(cfg.KafkaBrokers...),
		kgo.ConsumeTopics(cfg.Spans.Topic),
		kgo.ConsumerGroup(cfg.Spans.ConsumerGroup),
		// Marks are what MarkCommitRecords records; without this option both
		// it and CommitMarkedOffsets are silent no-ops. Autocommit can then
		// only ever commit offsets we have already stored.
		kgo.AutoCommitMarks(),
		// A rebalance between polling and committing would let us commit
		// offsets for partitions we no longer own. Blocking it is bounded by
		// one loop iteration, which AllowRebalance ends.
		kgo.BlockRebalanceOnPoll(),
	)
	if err != nil {
		return nil, fmt.Errorf("span consumer: %w", err)
	}

	// A separate client for the dead-letter topic: parking a message must not
	// depend on the state of the consumer's fetch session, and the two have
	// nothing else in common.
	producer, err := kgo.NewClient(kgo.SeedBrokers(cfg.KafkaBrokers...))
	if err != nil {
		consumer.Close()
		return nil, fmt.Errorf("dead-letter producer: %w", err)
	}

	return &Pipeline{
		consumer: consumer,
		producer: producer,
		writer:   newSpanWriter(pool),
		block:    newSpanBlock(),
		stages:   stages,
		metrics:  derive.NewWriter(pool),
		parked:   dlq.New(producer, cfg.Spans.DLQTopic, cfg.Spans.ConsumerGroup),
		seen:     dedupe.NewOffsets(),
		log:      log.With(slog.String("pipeline", string(config.PipelineTraceAssembly))),
		limits:   cfg.Spans,
		sampling: cfg.Sampling,
		now:      time.Now,
	}, nil
}

func samplingOptions(cfg config.Sampling) sample.Options {
	return sample.Options{
		Window:      cfg.Window,
		DecisionTTL: cfg.DecisionTTL,
		MaxTraces:   cfg.MaxTraces,
		MaxSpans:    cfg.MaxSpans,
	}
}

func samplingSource(cfg config.Config, opts Options) sample.PolicySource {
	if opts.Sampling != nil {
		return opts.Sampling
	}
	return sample.StaticPolicy(
		sample.DefaultPolicy(cfg.Sampling.SlowThreshold, cfg.Sampling.HealthyPercent))
}

func maskingSource(opts Options) mask.RulesSource {
	if opts.Masking != nil {
		return opts.Masking
	}
	return mask.StaticRules(mask.Defaults())
}

// Close leaves the consumer group and releases both clients.
func (p *Pipeline) Close() {
	p.consumer.Close()
	p.producer.Close()
}

// Run consumes until ctx is cancelled, then drains whatever it still holds.
//
// Each turn of the loop does the same four things: take in whatever has
// arrived, release the traces whose sampling window has closed, write the
// counters whose minute is complete, and commit the offsets of every batch
// whose output is now stored.
func (p *Pipeline) Run(ctx context.Context) error {
	p.log.InfoContext(ctx, "trace assembly started",
		slog.String("topic", p.limits.Topic),
		slog.String("group", p.limits.ConsumerGroup),
		slog.String("dlq_topic", p.limits.DLQTopic),
		slog.Int("batch_max_rows", p.limits.MaxRows),
		slog.Duration("batch_max_delay", p.limits.MaxDelay),
		slog.Duration("sampling_window", p.sampling.Window),
		slog.Int("sampling_healthy_percent", p.sampling.HealthyPercent),
		slog.Duration("sampling_slow_threshold", p.sampling.SlowThreshold),
	)
	p.running.Store(true)
	defer p.running.Store(false)
	defer p.log.InfoContext(ctx, "trace assembly stopped")

	var pending []*kgo.Record
	var flushBy time.Time

	for {
		if ctx.Err() != nil {
			return p.drain(ctx, pending)
		}

		fetches, err := p.poll(ctx, len(pending), flushBy)
		if errors.Is(err, errClientClosed) {
			// Only our own shutdown may close the consumer. Closed while the
			// context is still live means the pipeline is finished but the
			// process is not — it would go on serving a healthy /readyz while
			// consuming nothing, and ingest would be lost in silence. Returning
			// an error is what makes the process exit and the container restart.
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("consumer closed while the pipeline was running: %w", err)
		}
		if err != nil {
			return err
		}
		fetches.EachRecord(func(record *kgo.Record) { pending = append(pending, record) })
		if len(pending) > 0 && flushBy.IsZero() {
			flushBy = p.now().Add(p.limits.MaxDelay)
		}

		if p.batchIsReady(pending, flushBy) {
			if err := p.absorb(ctx, pending); err != nil {
				return err
			}
			pending, flushBy = pending[:0], time.Time{}
		}

		if err := p.settle(ctx); err != nil {
			return err
		}
		// Held since this iteration's poll, so nothing committed above could
		// have raced a reassignment.
		p.consumer.AllowRebalance()
	}
}

func (p *Pipeline) batchIsReady(pending []*kgo.Record, flushBy time.Time) bool {
	return len(pending) > 0 && (len(pending) >= p.limits.MaxRows || !p.now().Before(flushBy))
}

// poll waits for records, giving up at the flush deadline once a batch has
// started and after one batch delay otherwise. The second deadline is what
// keeps the loop turning while a tenant is quiet: the sampling window and the
// counter minutes both close on wall-clock time, not on arrivals.
func (p *Pipeline) poll(
	ctx context.Context,
	pending int,
	flushBy time.Time,
) (kgo.Fetches, error) {
	deadline := p.now().Add(p.limits.MaxDelay)
	if pending > 0 {
		deadline = flushBy
	}
	pollCtx, cancel := context.WithDeadline(ctx, deadline)
	defer cancel()

	fetches := p.consumer.PollRecords(pollCtx, p.limits.MaxRows-pending)
	if fetches.IsClientClosed() {
		return fetches, errClientClosed
	}
	return fetches, p.reportFetchErrors(ctx, fetches)
}

// errClientClosed ends Run when the consumer is closed under it. That is only
// benign during shutdown; see Run for why the distinction matters.
var errClientClosed = errors.New("kafka consumer closed")

// reportFetchErrors logs what franz-go will retry on its own and returns only
// what Run cannot continue past. A deadline of our own making is the poll timer
// firing, not a fault.
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

// absorb runs one polled batch through decode, mask, derive and sample, writes
// whatever the sampler released, and holds the batch's offsets until the rest
// of what it produced has been stored too.
func (p *Pipeline) absorb(ctx context.Context, records []*kgo.Record) error {
	now := p.now()
	entry := pendingCommit{
		positions:     make([]position, 0, len(records)),
		spansStoredAt: now.Add(p.sampling.Window),
	}

	p.block.reset()
	messages, spans := 0, 0
	for _, record := range records {
		entry.positions = append(entry.positions, positionOf(record))
		counted := p.seen.FirstDelivery(record.Topic, record.Partition, record.Offset)
		result, err := p.stages.absorbMessage(now, record.Value, counted)
		if err != nil {
			if parkErr := p.park(ctx, record, err); parkErr != nil {
				return parkErr
			}
			continue
		}
		p.appendRows(result.rows)
		entry.latestMinute = later(entry.latestMinute, result.latestMinute)
		spans += result.spans
		messages++
	}

	if err := p.insertBounded(ctx); err != nil {
		return err
	}
	p.commits.hold(entry)

	stats := p.stages.buffer.Stats()
	p.log.DebugContext(ctx, "batch absorbed",
		slog.Int("messages", messages),
		slog.Int("spans", spans),
		slog.Int("written_now", p.block.rows()),
		slog.Int("buffered_traces", stats.OpenTraces),
		slog.Int("buffered_spans", stats.BufferedSpans),
		slog.Int("uncommitted_records", p.commits.held()),
		slog.Uint64("spans_kept", stats.SpansKept),
		slog.Uint64("spans_dropped", stats.SpansDropped),
		slog.Uint64("traces_closed_early", stats.ClosedEarly),
	)
	return nil
}

// settle releases the traces whose window has closed, writes the counters whose
// minute is complete, and commits every batch that is now fully stored.
func (p *Pipeline) settle(ctx context.Context) error {
	now := p.now()

	p.block.reset()
	p.appendRows(p.stages.buffer.Expire(now))
	if err := p.insertBounded(ctx); err != nil {
		return err
	}

	counterCtx, cancelCounters := bounded(ctx)
	defer cancelCounters()
	if err := p.metrics.Insert(counterCtx, p.stages.counters.Drain(now)); err != nil {
		return err
	}

	due := p.commits.ready(now, func(minute time.Time) bool {
		return p.stages.counters.Drained(now, minute)
	})
	return p.commit(ctx, due)
}

// drain writes everything still held and commits it, which is what makes a
// planned shutdown lose nothing. It runs on a context of its own: the one that
// ended the loop is already cancelled, and cancelled contexts cannot insert.
func (p *Pipeline) drain(ctx context.Context, unprocessed []*kgo.Record) error {
	drainCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), drainTimeout)
	defer cancel()

	// Records polled but never absorbed are simply not committed: they are
	// still on the broker and the next owner of the partition reads them.
	if len(unprocessed) > 0 {
		p.log.InfoContext(drainCtx, "leaving unprocessed records for redelivery",
			slog.Int("records", len(unprocessed)))
	}

	p.block.reset()
	p.appendRows(p.stages.buffer.Drain(p.now()))
	if err := p.writer.insert(drainCtx, p.block); err != nil {
		return err
	}
	if err := p.metrics.Insert(drainCtx, p.stages.counters.DrainAll()); err != nil {
		return err
	}

	stats := p.stages.buffer.Stats()
	p.log.InfoContext(drainCtx, "trace assembly drained",
		slog.Int("rows_written", p.block.rows()),
		slog.Int("records_committed", p.commits.held()),
		slog.Uint64("spans_kept", stats.SpansKept),
		slog.Uint64("spans_dropped", stats.SpansDropped),
	)
	return p.commit(drainCtx, p.commits.drain())
}

func (p *Pipeline) commit(ctx context.Context, positions []position) error {
	marks := markable(positions)
	if marks == nil {
		return nil
	}
	p.consumer.MarkCommitOffsets(marks)
	commitCtx, cancel := bounded(ctx)
	defer cancel()
	if err := p.consumer.CommitMarkedOffsets(commitCtx); err != nil {
		return fmt.Errorf("commit offsets: %w", err)
	}
	return nil
}

// park sends an undecodable message to the dead-letter topic. Its offset is
// only committed with the rest of its batch, so a park that fails is retried by
// re-consuming rather than silently dropping the message.
func (p *Pipeline) park(ctx context.Context, record *kgo.Record, cause error) error {
	parkCtx, cancel := bounded(ctx)
	defer cancel()
	if err := p.parked.Park(parkCtx, record, cause.Error(), dlq.Context{}); err != nil {
		return err
	}
	p.log.WarnContext(ctx, "parked an undecodable span message",
		slog.String("topic", record.Topic),
		slog.Int("partition", int(record.Partition)),
		slog.Int64("offset", record.Offset),
		slog.String("dlq_topic", p.limits.DLQTopic),
		slog.String("error", cause.Error()),
	)
	return nil
}

func (p *Pipeline) appendRows(rows []spanRow) {
	for _, row := range rows {
		p.block.append(row)
	}
}

func later(current, candidate time.Time) time.Time {
	if candidate.After(current) {
		return candidate
	}
	return current
}

// Running reports whether the consume loop is live. Registered as a readiness
// probe so a stopped pipeline turns the container red instead of leaving it
// healthy and idle.
func (p *Pipeline) Running(context.Context) error {
	if !p.running.Load() {
		return errors.New("trace assembly is not running")
	}
	return nil
}

// insertBounded writes the accumulated block under a deadline.
func (p *Pipeline) insertBounded(ctx context.Context) error {
	writeCtx, cancel := bounded(ctx)
	defer cancel()
	return p.writer.insert(writeCtx, p.block)
}

// externalCallTimeout bounds every call the loop makes out of the process.
//
// Without it a connection killed underneath the pool never returns: chpool dials
// with a timeout but an established socket that dies has none, so an insert after
// a ClickHouse restart blocks forever. The loop then stops consuming while the
// process stays up, the goroutine stays live, and readiness stays green — a wedge
// that reports healthy, which is worse than a crash because nothing restarts it.
// Bounded, the same failure surfaces as an error, and an error exits the process.
const externalCallTimeout = 30 * time.Second

// bounded returns a context for one call out of the process. The caller must
// cancel it.
func bounded(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, externalCallTimeout)
}
