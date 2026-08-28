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
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// drainTimeout bounds the final write on shutdown. It is short because the work
// is one insert of what is already in memory, and because a worker that will
// not exit gets killed instead of drained.
const drainTimeout = 15 * time.Second

// heartbeatInterval is how often the pipeline says what it is holding. Without
// it a healthy worker and a dead one are identical in the logs: one startup
// line and then nothing. Periodic rather than per-batch, because a batch closes
// every couple of seconds and a line each would be unreadable under load.
//
// Operational logging to stdout, not the self-instrumentation ADR 0053 defers —
// it is the container-log fallback that ADR's second rule keeps independent of
// the OTel pipeline, for reading when that pipeline is the broken thing.
const heartbeatInterval = 30 * time.Second

// Options are the collaborators the pipeline does not own.
//
// Both are optional. A worker with no control-plane connection masks with the
// compiled-in defaults and samples with the deployment default policy, which is
// how the stages behave for a project that has configured neither.
type Options struct {
	Masking   mask.RulesSource
	Sampling  sample.PolicySource
	Retention RetentionSource
}

// RetentionSource answers how long one project's data is kept. Narrow on
// purpose, like the two above it: the stages need an answer, not the cache.
type RetentionSource interface {
	Retention(projectID string) (raw, derived uint16)
}

// staticRetention answers the same for every project. The deployment default
// when nothing has told the worker otherwise.
type staticRetention struct{ raw, derived uint16 }

func (s staticRetention) Retention(string) (uint16, uint16) { return s.raw, s.derived }

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

	// ingest is the durable half of `seen`: the per-partition record of what has
	// already been counted, which unlike the in-process watermark survives both
	// this process and the store restarting (ADR 0061).
	ingest *ingeststate.Guard

	// running is what readiness reports. A worker whose pipeline has stopped is
	// not ready, however healthy its dependencies look — the failure that taught
	// us this presented as a green container consuming nothing for nine minutes.
	running atomic.Bool

	// retimedSpans counts spans whose own timestamp was too far from receive
	// time to bucket on. A number that climbs names a customer with a broken
	// clock, and the warning that recorded each batch says which one.
	retimedSpans uint64

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

	ingest := ingeststate.NewGuard(ingeststate.NewKeeperMap(pool))

	consumer, err := kgo.NewClient(
		kgo.SeedBrokers(cfg.KafkaBrokers...),
		kgo.ConsumeTopics(cfg.Spans.Topic),
		kgo.ConsumerGroup(cfg.Spans.ConsumerGroup),
		// A partition can arrive from another worker, whose settlements this
		// process never saw. Re-reading the durable state before counting
		// anything is what keeps the handover from counting a minute twice.
		kgo.OnPartitionsAssigned(func(context.Context, *kgo.Client, map[string][]int32) {
			ingest.Invalidate()
		}),
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
		ingest:   ingest,
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

func retentionSource(cfg config.Config, opts Options) RetentionSource {
	if opts.Retention != nil {
		return opts.Retention
	}
	return staticRetention{raw: cfg.Retention.RawDays, derived: cfg.Retention.DerivedDays}
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
	nextHeartbeat := p.now().Add(heartbeatInterval)

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
		if now := p.now(); !now.Before(nextHeartbeat) {
			p.heartbeat(ctx, now)
			nextHeartbeat = now.Add(heartbeatInterval)
		}
		// Held since this iteration's poll, so nothing committed above could
		// have raced a reassignment.
		p.consumer.AllowRebalance()
	}
}

// heartbeat records what the pipeline is holding, at INFO, on a timer.
//
// Field names follow the control plane's request log — snake_case, and a
// duration named `*_ms` and written as an integer — so one query reads the data
// plane and the control plane together.
func (p *Pipeline) heartbeat(ctx context.Context, now time.Time) {
	stats := p.stages.buffer.Stats()
	p.log.InfoContext(ctx, "ingest heartbeat",
		slog.String("topic", p.limits.Topic),
		slog.Int("buffered_traces", stats.OpenTraces),
		slog.Int("buffered_spans", stats.BufferedSpans),
		slog.Int("uncommitted_records", p.commits.held()),
		// The two that show a wedge: commits that stop advancing while the
		// counter map only grows.
		slog.Int64("commit_lag_ms", p.commits.commitLag(now).Milliseconds()),
		slog.Int("counter_series", p.stages.counters.Pending()),
		slog.Uint64("spans_kept", stats.SpansKept),
		slog.Uint64("spans_dropped", stats.SpansDropped),
		slog.Uint64("traces_closed_early", stats.ClosedEarly),
		slog.Uint64("spans_retimed", p.retimedSpans),
	)
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
	counting, err := p.alreadyCounted(ctx, records)
	if err != nil {
		return err
	}

	now := p.now()
	entry := pendingCommit{
		positions:     make([]position, 0, len(records)),
		polledAt:      now,
		spansStoredAt: now.Add(p.sampling.Window),
	}

	p.block.reset()
	messages, spans := 0, 0
	var skew skewReport
	for _, record := range records {
		entry.positions = append(entry.positions, positionOf(record))
		// Both watermarks have to be consulted and the in-process one has to be
		// updated either way, so neither call may be short-circuited away.
		firstDelivery := p.seen.FirstDelivery(record.Topic, record.Partition, record.Offset)
		partition := ingeststate.Partition{Topic: record.Topic, ID: record.Partition}
		counted := firstDelivery && counting.Counts(partition, record.Offset)
		result, err := p.stages.absorbMessage(now, record.Value, counted)
		if err != nil {
			if parkErr := p.park(ctx, record, err); parkErr != nil {
				return parkErr
			}
			continue
		}
		p.appendRows(result.rows)
		entry.latestMinute = later(entry.latestMinute, result.latestMinute)
		skew.merge(result.skew)
		spans += result.spans
		messages++
	}

	if err := p.insertBounded(ctx); err != nil {
		return err
	}
	p.commits.hold(entry)
	p.reportSkew(ctx, skew)

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

// reportSkew says once per batch that a client's clock was too far out to
// count on, naming the worst offender. Once per batch rather than once per
// span: a whole fleet with the wrong time would otherwise write a log line per
// span, and the line is a pointer to go and look, not a census.
func (p *Pipeline) reportSkew(ctx context.Context, skew skewReport) {
	if skew.spans == 0 {
		return
	}
	p.retimedSpans += skew.spans
	p.log.WarnContext(ctx, "counted spans at receive time; the client clock is too far out",
		slog.Uint64("spans", skew.spans),
		slog.String("tenant_id", skew.tenantID),
		slog.String("project_id", skew.projectID),
		slog.Int64("worst_skew_ms", skew.worst.Milliseconds()),
	)
}

// alreadyCounted resolves a polled batch against the durable per-partition
// state, so a replay of offsets whose counters are already stored is not
// counted a second time. Its spans still travel — an identical span row is
// idempotent — and only the counters are held back.
func (p *Pipeline) alreadyCounted(
	ctx context.Context,
	records []*kgo.Record,
) (ingeststate.Decisions, error) {
	if err := p.retryTransient(ctx, "read counted offsets", p.ingest.Refresh); err != nil {
		return nil, err
	}

	decisions := p.ingest.Counting(incomingRanges(records))
	for partition, decision := range decisions {
		if decision.Scenario == ingeststate.New && !decision.Ambiguous {
			continue
		}
		p.log.InfoContext(ctx, "resolving a replayed batch against the counted offsets",
			slog.String("topic", partition.Topic),
			slog.Int("partition", int(partition.ID)),
			slog.String("scenario", string(decision.Scenario)),
			slog.Int64("count_from", decision.CountFrom),
			// True only when the previous settlement never confirmed, so the
			// store's own deduplication is all that stops a duplicate here.
			slog.Bool("ambiguous", decision.Ambiguous),
		)
	}
	return decisions, nil
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

	drained := func(minute time.Time) bool { return p.stages.counters.Drained(now, minute) }
	reached := p.commits.countedThrough(drained)
	if err := p.settleCounters(ctx, reached, p.stages.counters.Drain(now)); err != nil {
		return err
	}

	return p.commit(ctx, p.commits.ready(now, drained))
}

// settleCounters writes one drain of counters, bracketed by the durable record
// of the offsets it makes exact.
//
// The order is the whole point (ADR 0061): BEFORE says an insert of this range
// is in flight, AFTER says the store took it, and the Kafka offsets move only
// after that — so a worker that comes back to a restarted store can tell a
// finished insert from an interrupted one instead of guessing.
func (p *Pipeline) settleCounters(
	ctx context.Context,
	reached map[ingeststate.Partition]ingeststate.Range,
	points []derive.Point,
) error {
	settlement := p.ingest.Advance(reached)
	if err := p.retryTransient(ctx, "record counted offsets", func(callCtx context.Context) error {
		return p.ingest.Begin(callCtx, settlement)
	}); err != nil {
		return err
	}
	if err := p.insertCounters(ctx, points); err != nil {
		return err
	}
	return p.retryTransient(ctx, "confirm counted offsets", func(callCtx context.Context) error {
		return p.ingest.Complete(callCtx, settlement)
	})
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
	// Everything held is drained here, so every held offset is counted through.
	reached := p.commits.countedThrough(func(time.Time) bool { return true })
	if err := p.settleCounters(drainCtx, reached, p.stages.counters.DrainAll()); err != nil {
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
	if err := p.retryTransient(ctx, "commit offsets", p.consumer.CommitMarkedOffsets); err != nil {
		return fmt.Errorf("commit offsets: %w", err)
	}
	return nil
}

// park sends an undecodable message to the dead-letter topic. Its offset is
// only committed with the rest of its batch, so a park that fails is retried by
// re-consuming rather than silently dropping the message.
//
// The message is attributed to the tenant and project the envelope named. That
// is not decoration: a parked message skipped the mask stage by construction,
// so the dead-letter topic holds unmasked customer data, and an exposure nobody
// can attribute is one nobody can disclose or delete.
func (p *Pipeline) park(ctx context.Context, record *kgo.Record, cause error) error {
	details := parkContext(cause)
	if err := p.retryTransient(ctx, "park to dead-letter", func(callCtx context.Context) error {
		return p.parked.Park(callCtx, record, cause.Error(), details)
	}); err != nil {
		return err
	}
	p.log.WarnContext(ctx, "parked an undecodable span message",
		slog.String("topic", record.Topic),
		slog.Int("partition", int(record.Partition)),
		slog.Int64("offset", record.Offset),
		slog.String("dlq_topic", p.limits.DLQTopic),
		slog.String("tenant_id", details.TenantID),
		slog.String("project_id", details.ProjectID),
		slog.String("error", cause.Error()),
	)
	return nil
}

// parkContext reads the coordinates the decoder established. An error carrying
// none means the bytes were not our envelope at all, and the parked message is
// honestly unattributed rather than attributed to a guess.
func parkContext(cause error) dlq.Context {
	var decodeErr *rejected
	if !errors.As(cause, &decodeErr) {
		return dlq.Context{}
	}
	return dlq.Context{
		IngestID:  decodeErr.ingestID,
		TenantID:  decodeErr.tenantID,
		ProjectID: decodeErr.projectID,
	}
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

// Retry budget for a call out of the process.
//
// A broker or store that is briefly unreachable is the normal weather of a
// distributed system, not a reason to end the worker. Nothing is lost by
// waiting: offsets stay uncommitted, so the records are still on the broker,
// and exiting would only discard the in-memory buffer and re-consume them
// anyway. Past the budget the outage is no longer brief, and ending the process
// is the honest answer — a restart at least re-dials from scratch.
const (
	retryBudget      = 2 * time.Minute
	retryFirstDelay  = 250 * time.Millisecond
	retryMaxDelay    = 5 * time.Second
	retryDelayFactor = 2
)

// retryTransient runs op until it succeeds, the budget runs out, or the worker
// is asked to stop.
//
// franz-go retries its own requests (RequestRetries, 20 by default, backing off
// 250ms–2.5s), so wrapping a Kafka call looks redundant. It is not: before this
// existed the pipeline died on `commit offsets: unable to dial: lookup kafka: no
// such host` during a twenty-second broker outage, which franz-go's own retries
// did not absorb. Measured, not assumed — do not remove it without reproducing
// that.
func (p *Pipeline) retryTransient(ctx context.Context, what string, op func(context.Context) error) error {
	deadline := p.now().Add(retryBudget)
	delay := retryFirstDelay

	for attempt := 1; ; attempt++ {
		callCtx, cancel := bounded(ctx)
		err := op(callCtx)
		cancel()
		if err == nil {
			if attempt > 1 {
				p.log.InfoContext(ctx, "recovered after a transient failure",
					slog.String("operation", what),
					slog.Int("attempts", attempt),
				)
			}
			return nil
		}
		if ctx.Err() != nil {
			return err
		}
		if !p.now().Add(delay).Before(deadline) {
			return fmt.Errorf("%s failed for %s: %w", what, retryBudget, err)
		}

		p.log.WarnContext(ctx, "transient failure, retrying",
			slog.String("operation", what),
			slog.Int("attempt", attempt),
			slog.Duration("retry_in", delay),
			slog.String("error", err.Error()),
		)
		select {
		case <-ctx.Done():
			return err
		case <-time.After(delay):
		}
		if delay *= retryDelayFactor; delay > retryMaxDelay {
			delay = retryMaxDelay
		}
	}
}

// insertCounters writes derived counters, retrying without ever writing twice.
//
// The retry is what makes a brief store outage survivable, and it is also the
// only way left to double-count: if an insert lands and only its acknowledgement
// is lost, sending it again duplicates the minute. ClickHouse normally catches
// that, but not across the restart that tends to cause it (ADR 0061).
//
// So every attempt after the first asks whether the write already landed. A
// block is atomic, so one point answers for the batch.
func (p *Pipeline) insertCounters(ctx context.Context, points []derive.Point) error {
	if len(points) == 0 {
		return nil
	}
	attempted := false
	return p.retryTransient(ctx, "insert counters", func(callCtx context.Context) error {
		if attempted {
			applied, err := p.metrics.Applied(callCtx, points[0])
			if err != nil {
				return err // The store is still unreachable; retry the check.
			}
			if applied {
				p.log.InfoContext(callCtx, "counters had already landed; not writing them again",
					slog.Time("minute", points[0].Timestamp))
				return nil
			}
		}
		attempted = true
		return p.metrics.Insert(callCtx, points)
	})
}

// insertBounded writes the accumulated block under a deadline.
func (p *Pipeline) insertBounded(ctx context.Context) error {
	return p.retryTransient(ctx, "insert spans", func(callCtx context.Context) error {
		return p.writer.insert(callCtx, p.block)
	})
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
