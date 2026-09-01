// Package traceassembly runs the trace-assembly pipeline: it consumes span
// envelopes from Kafka, flattens them to rows, buffers each trace until tail
// sampling can see it whole, and writes what survives to ClickHouse.
//
// Only the stages are here. The machinery around them — the consume loop, the
// offsets, the retry budget, dead-letter parking and the heartbeat — is
// internal/pipeline/consume, which every signal's pipeline shares.
package traceassembly

import (
	"context"
	"log/slog"
	"time"

	"github.com/ClickHouse/ch-go/chpool"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dedupe"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/serviceedges"
)

// Options are the collaborators the pipeline does not own.
//
// All three are optional. A worker with no control-plane connection masks with
// the compiled-in defaults and samples with the deployment default policy,
// which is how the stages behave for a project that has configured neither.
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

// Pipeline is the trace half of consume → decode → transform → write →
// commit-when-stored: it runs span envelopes through decode → mask → derive →
// sample → sink, and the loop around it moves the offsets.
//
// Two orderings in that list are invariants rather than preferences. `mask`
// comes first because an unmasked value must never reach a counter, a buffer or
// the store. `derive` comes before `sample` because counters are computed from
// every trace, which is what keeps them exact while storage keeps a tenth of
// the healthy ones (ADR 0010).
//
// Offsets commit only after everything a batch produced has been stored —
// spans out of the sampling buffer, counters out of their minute bucket, which
// is what spanStorage remembers — making delivery at-least-once. A replay must
// therefore be recognised twice over: re-counting a span is a wrong number, and
// re-inserting one is a row a customer sees twice in their trace. `seen` covers
// a redelivery inside this process and `ingest` covers one across a restart.
type Pipeline struct {
	loop *consume.Loop[spanStorage]

	writer *spanWriter
	block  *spanBlock

	// summaries fills `trace_summaries`, the per-trace row the trace list is a
	// sort-key read of. It is fed the rows the sampler released — after the
	// sampler, unlike every other derived table here — because a list naming
	// traces whose spans were never stored would be worse than useless.
	summaries     *summaries
	summaryWriter *summaryWriter

	stages  *stages
	metrics *derive.Writer
	// endpoints writes `endpoint_stats_1m`. It is filled here rather than by a
	// materialized view over `spans` (dropped in 000024): a view is downstream
	// of the sampler, so the service overview read a tenth of the traffic at
	// ten times the error rate.
	endpoints *derive.EndpointWriter
	edges     *serviceedges.Writer
	seen      *dedupe.Offsets

	// ingest is the durable half of `seen`: the per-partition record of what has
	// already been counted and what has already been written, which unlike the
	// in-process watermark survives both this process and the store restarting
	// (ADR 0061).
	ingest *ingeststate.Guard

	// alreadyStoredSpans counts spans a replay did not write because the
	// durable watermark says ClickHouse already has them. It climbs in a step
	// after every restart and is flat otherwise; before this existed, every one
	// of those spans became a second copy in the customer's trace.
	//
	// int, unlike the running totals beside it, because that is the width the
	// per-batch span count already has: agreeing removes a conversion rather
	// than adding a bounds check to justify one.
	alreadyStoredSpans int

	// retimedSpans counts spans whose own timestamp was too far from receive
	// time to bucket on. A number that climbs names a customer with a broken
	// clock, and the warning that recorded each batch says which one.
	retimedSpans uint64

	// disownedSpans counts spans taken back out of the sampling buffer because
	// their partition left for another worker. It steps at every handover and
	// is flat otherwise; each one is a row this process would have written and
	// the new owner would have written again (ADR 0090).
	disownedSpans int

	// topic is the one this pipeline consumes, so a rebalance callback can tell
	// which of its partitions are ours.
	topic string

	log      *slog.Logger
	sampling config.Sampling
}

// spanStorage is what one batch still owes storage: the moment its spans have
// certainly left the sampling buffer, and the newest counter bucket it fed.
//
// Both have to be reached before its offsets may move. Every trace a record
// touched opened at or before the record was processed, so all of them are
// decided by then plus the sampling window; and a batch's counters are only
// safe once every minute bucket it fed has been drained.
type spanStorage struct {
	spansStoredAt time.Time
	latestMinute  time.Time
	// latestEdgeMinute settles later than latestMinute: an edge waits for its
	// partner span before it exists at all, so offsets wait for it separately.
	latestEdgeMinute time.Time
}

// New builds the pipeline and joins the consumer group. The caller owns the
// ClickHouse pool; Close releases only what New created.
func New(cfg config.Config, pool *chpool.Pool, log *slog.Logger, opts Options) (*Pipeline, error) {
	stages, err := newStages(cfg, opts)
	if err != nil {
		return nil, err
	}

	ingest := ingeststate.NewGuard(ingeststate.NewKeeperMap(pool))
	pipeline := &Pipeline{
		writer:        newSpanWriter(pool),
		block:         newSpanBlock(),
		summaries:     newSummaries(cfg.Sampling.DecisionTTL, cfg.Sampling.MaxTraces),
		summaryWriter: newSummaryWriter(pool),
		stages:        stages,
		metrics:       derive.NewWriter(pool),
		endpoints:     derive.NewEndpointWriter(pool),
		edges:         serviceedges.NewWriter(pool),
		seen:          dedupe.NewOffsets(),
		ingest:        ingest,
		topic:         cfg.Spans.Topic,
		log:           log.With(slog.String("pipeline", string(config.PipelineTraceAssembly))),
		sampling:      cfg.Sampling,
	}

	loop, err := consume.Dial[spanStorage](consume.Settings{
		Name:          string(config.PipelineTraceAssembly),
		Brokers:       cfg.KafkaBrokers,
		Topic:         cfg.Spans.Topic,
		ConsumerGroup: cfg.Spans.ConsumerGroup,
		DLQTopic:      cfg.Spans.DLQTopic,
		MaxMessages:   cfg.Spans.MaxMessages,
		MaxDelay:      cfg.Spans.MaxDelay,
		// A partition can arrive from another worker, whose settlements this
		// process never saw. Re-reading the durable state before counting
		// anything is what keeps the handover from counting a minute twice.
		OnAssigned: ingest.Invalidate,
		// The other half of the same fact. A partition leaving takes this
		// process's buffered spans with it, or they are written here and again
		// by the worker that took it over.
		OnRevoked: pipeline.release,
		OnLost:    pipeline.releaseLost,
	}, log)
	if err != nil {
		return nil, err
	}

	pipeline.loop = loop
	return pipeline, nil
}

// release drops what the pipeline still holds for partitions the group has
// taken back while the session is live.
func (p *Pipeline) release(disowned map[string][]int32) {
	partitions := disowned[p.topic]
	if len(partitions) == 0 {
		return
	}

	dropped := p.stages.buffer.Disown(partitions)
	// The in-process replay guard has to forget them at the same instant. Left
	// in place, a partition that came back would have these very records
	// recognised as replays and skipped — and their spans were dropped, not
	// stored, so that is a hole rather than a duplicate.
	p.seen.Forget(p.topic, partitions)
	p.disownedSpans += dropped.Spans

	p.log.LogAttrs(context.Background(), slog.LevelInfo, "released buffered spans with their partitions",
		slog.String("topic", p.topic),
		slog.Any("partitions", partitions),
		slog.Int("spans", dropped.Spans),
		slog.Int("traces", dropped.Traces),
	)
}

// releaseLost is release for a session that is already gone.
//
// The release itself is identical, and that is the point: dropping claims
// nothing, so it is as safe against a broker that has stopped believing in us
// as it is against one that has not. What differs is the durable cache — these
// partitions may have belonged to another worker for a while, and its
// settlements are not in this process's copy — so it is marked for re-reading
// here rather than waiting for an assignment that may never come.
func (p *Pipeline) releaseLost(disowned map[string][]int32) {
	p.release(disowned)
	p.ingest.Invalidate()
}

func samplingOptions(cfg config.Sampling) sample.Options {
	return sample.Options{
		Window:      cfg.Window,
		DecisionTTL: cfg.DecisionTTL,
		MaxTraces:   cfg.MaxTraces,
		MaxSpans:    cfg.MaxSpans,
		FirstTraces: cfg.FirstTraces,
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

// Close leaves the consumer group and releases the broker clients.
func (p *Pipeline) Close() { p.loop.Close() }

// Run consumes until ctx is cancelled, then drains whatever it still holds.
func (p *Pipeline) Run(ctx context.Context) error { return p.loop.Run(ctx, p) }

// Running reports whether the consume loop is live, for the readiness probe.
func (p *Pipeline) Running(ctx context.Context) error { return p.loop.Running(ctx) }

// Describe is what the startup line says about this pipeline beyond its topic
// and batch limits.
func (p *Pipeline) Describe() []slog.Attr {
	return []slog.Attr{
		slog.Duration("sampling_window", p.sampling.Window),
		slog.Int("sampling_healthy_percent", p.sampling.HealthyPercent),
		slog.Duration("sampling_slow_threshold", p.sampling.SlowThreshold),
	}
}

// Holding is what the heartbeat says about the spans and counters in memory.
// The two that show a wedge are the counter map only growing and, from the loop,
// commits that stop advancing.
func (p *Pipeline) Holding() []slog.Attr {
	stats := p.stages.buffer.Stats()
	edges := p.stages.edges.Stats()
	summaries := p.summaries.Stats()
	return []slog.Attr{
		slog.Int("buffered_traces", stats.OpenTraces),
		slog.Int("buffered_spans", stats.BufferedSpans),
		slog.Int("counter_series", p.stages.counters.Pending()),
		slog.Int("endpoint_series", p.stages.counters.PendingEndpoints()),
		slog.Uint64("spans_kept", stats.SpansKept),
		slog.Uint64("spans_dropped", stats.SpansDropped),
		slog.Uint64("traces_closed_early", stats.ClosedEarly),
		slog.Uint64("spans_retimed", p.retimedSpans),
		slog.Int("spans_already_stored", p.alreadyStoredSpans),
		slog.Int("spans_disowned", p.disownedSpans),
		slog.Int("summarised_traces", summaries.OpenTraces),
		slog.Uint64("trace_summaries_written", summaries.Written),
		// Revised counts traces that gained a span after their row was written.
		// Flat is the healthy shape; a number that climbs says the sampling
		// window is closing before traces are whole.
		slog.Uint64("trace_summaries_revised", summaries.Revised),
		slog.Uint64("trace_summaries_evicted", summaries.Evicted),
		// Traces whose endpoint set hit its cap, so an operation filter answers
		// them from a partial set. It should never move.
		slog.Uint64("trace_summaries_truncated", summaries.EndpointsTruncated),
		slog.Int("edge_pairs_pending", edges.PendingSpans),
		slog.Int("edge_series", edges.OpenEdges),
		// The gap between these two is how many unanswered calls the caller's
		// own peer attributes were able to name.
		slog.Uint64("edges_unanswered", edges.Unanswered),
		slog.Uint64("edges_unidentified", edges.Unidentified),
	}
}

// Stored reports whether a batch's spans have left the sampling buffer and
// everything derived from them has reached ClickHouse. Until both, its offsets
// stay uncommitted.
func (p *Pipeline) Stored(now time.Time, produced spanStorage) bool {
	return !now.Before(produced.spansStoredAt) &&
		p.stages.counters.Drained(now, produced.latestMinute) &&
		p.stages.edges.Drained(now, produced.latestEdgeMinute)
}

// Absorb runs one polled batch through decode, mask, derive and sample, and
// writes whatever the sampler released.
func (p *Pipeline) Absorb(ctx context.Context, polled consume.Polled) (spanStorage, error) {
	counting, err := p.alreadySettled(ctx, polled.Positions)
	if err != nil {
		return spanStorage{}, err
	}

	produced := spanStorage{spansStoredAt: polled.At.Add(p.sampling.Window)}
	p.block.reset()
	messages, spans := 0, 0
	var skew skewReport
	for _, record := range polled.Records {
		// Both watermarks have to be consulted and the in-process one has to be
		// updated either way, so neither call may be short-circuited away.
		firstDelivery := p.seen.FirstDelivery(record.Topic, record.Partition, record.Offset)
		partition := ingeststate.Partition{Topic: record.Topic, ID: record.Partition}
		run := handling{
			count:     firstDelivery && counting.Counts(partition, record.Offset),
			store:     firstDelivery && counting.Stores(partition, record.Offset),
			partition: record.Partition,
		}
		result, err := p.stages.absorbMessage(polled.At, record.Value, run)
		if err != nil {
			if parkErr := p.loop.Park(ctx, record, err); parkErr != nil {
				return spanStorage{}, parkErr
			}
			continue
		}
		if !run.store {
			p.alreadyStoredSpans += result.spans
		}
		p.appendRows(polled.At, result.rows)
		produced.latestMinute = later(produced.latestMinute, result.latestMinute)
		produced.latestEdgeMinute = later(produced.latestEdgeMinute, result.latestEdgeMinute)
		skew.merge(result.skew)
		spans += result.spans
		messages++
	}

	if err := p.insertBounded(ctx); err != nil {
		return spanStorage{}, err
	}
	if err := p.insertSummaries(ctx, p.summaries.Drain(polled.At)); err != nil {
		return spanStorage{}, err
	}
	p.reportSkew(ctx, skew)

	stats := p.stages.buffer.Stats()
	p.log.DebugContext(ctx, "batch absorbed",
		slog.Int("messages", messages),
		slog.Int("spans", spans),
		slog.Int("written_now", p.block.rows()),
		slog.Int("buffered_traces", stats.OpenTraces),
		slog.Int("buffered_spans", stats.BufferedSpans),
		slog.Uint64("spans_kept", stats.SpansKept),
		slog.Uint64("spans_dropped", stats.SpansDropped),
		slog.Uint64("traces_closed_early", stats.ClosedEarly),
	)
	return produced, nil
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

// alreadySettled resolves a polled batch against the durable per-partition
// state, so a replay is neither counted nor written a second time.
//
// The read is free: the guard's cache is already in memory and is only re-read
// when a rebalance invalidates it, which is the same Refresh the counters have
// always done.
func (p *Pipeline) alreadySettled(
	ctx context.Context,
	positions []consume.Position,
) (ingeststate.Decisions, error) {
	if err := p.loop.Retry(ctx, "read counted offsets", p.ingest.Refresh); err != nil {
		return nil, err
	}

	incoming := ingeststate.RangesOf(positions)
	decisions := p.ingest.Counting(incoming)
	for partition, decision := range decisions {
		if !holdsAnythingBack(decision, incoming[partition]) {
			continue
		}
		p.log.InfoContext(ctx, "resolving a replayed batch against the settled offsets",
			slog.String("topic", partition.Topic),
			slog.Int("partition", int(partition.ID)),
			slog.String("scenario", string(decision.Scenario)),
			slog.Int64("count_from", decision.CountFrom),
			// The band between the two is the replay this skips writing: spans
			// already in ClickHouse whose counters are not yet exact. It is the
			// common case after a restart, and the counter scenario alone reads
			// "new" throughout it — which is why this line watches both.
			slog.Int64("store_from", decision.StoreFrom),
			slog.Int64("batch_from", incoming[partition].Min),
			// True only when the previous settlement never confirmed, so the
			// store's own deduplication is all that stops a duplicate here.
			slog.Bool("ambiguous", decision.Ambiguous),
		)
	}
	return decisions, nil
}

// holdsAnythingBack reports whether a decision actually withholds part of this
// batch from a stage, which is what makes it worth a log line.
func holdsAnythingBack(decision ingeststate.Decision, batch ingeststate.Range) bool {
	replayedCounters := decision.Scenario != ingeststate.New || decision.Ambiguous
	return replayedCounters || decision.StoreFrom > batch.Min
}

// Settle releases the traces whose window has closed and writes the counters
// whose minute is complete. The loop commits what that made storable.
func (p *Pipeline) Settle(ctx context.Context, now time.Time) error {
	p.block.reset()
	p.appendRows(now, p.stages.buffer.Expire(now))
	if err := p.insertBounded(ctx); err != nil {
		return err
	}
	// Before the watermark below claims these spans are stored: a summary lost
	// to a crash after the watermark moved would never be re-derived, and the
	// trace would be missing from the list for as long as its spans are kept.
	if err := p.insertSummaries(ctx, p.summaries.Drain(now)); err != nil {
		return err
	}

	// Derived output alone, without waiting for the sampling window: a batch's
	// spans can still be in the buffer while everything derived from them is
	// already exact. Both counters and edges must be drained, because both are
	// summing rollups whose offsets must not move before they reach storage.
	derivedDrained := func(produced spanStorage) bool {
		return p.stages.counters.Drained(now, produced.latestMinute) &&
			p.stages.edges.Drained(now, produced.latestEdgeMinute)
	}
	reached := ingeststate.RangesOf(p.loop.Reached(derivedDrained))
	return p.settleDerived(ctx, p.spansWritten(now), reached,
		p.stages.counters.Drain(now), p.stages.edges.Drain(now))
}

// spansWritten is how far each partition's spans have certainly reached
// ClickHouse: the prefix of held batches whose sampling window has closed.
//
// It is computed after Settle's insert, never before. Every trace a record
// touched opened at or before the record was absorbed, so all of them are
// decided by that instant plus the window, and the rows they released are in
// the block the caller has just written.
//
// This runs a sampling window behind ingest while the offsets themselves run a
// minute bucket behind it, which is the whole value of the watermark: the band
// between the two is the part of a replay that is already stored.
func (p *Pipeline) spansWritten(now time.Time) ingeststate.Written {
	return p.ingest.Writing(ingeststate.RangesOf(p.loop.Reached(spansStored(now))))
}

// spansStored reports whether every span a batch carried has left the sampling
// buffer. A trace closes one window after the record that opened it was
// absorbed, and spanStorage records that instant per batch.
func spansStored(now time.Time) func(spanStorage) bool {
	return func(produced spanStorage) bool { return !now.Before(produced.spansStoredAt) }
}

// settleDerived writes one drain of counters, bracketed by the durable record
// of the offsets it makes exact, and records how far the spans are written.
//
// The order is the whole point (ADR 0061): BEFORE says an insert of this range
// is in flight, AFTER says the store took it, and the Kafka offsets move only
// after that — so a worker that comes back to a restarted store can tell a
// finished insert from an interrupted one instead of guessing.
//
// The span watermark obeys the same rule in its simpler form. It is written
// after ClickHouse has acknowledged the spans and before the loop commits the
// offsets, in that order and no other: recorded early, a failed insert would
// leave the replay skipping spans nothing ever stored, and a hole cannot be
// repaired where a duplicate row can.
func (p *Pipeline) settleDerived(
	ctx context.Context,
	written ingeststate.Written,
	reached map[ingeststate.Partition]ingeststate.Range,
	derived derive.Derived,
	edges []serviceedges.Edge,
) error {
	settlement := p.ingest.Advance(reached)
	if err := p.loop.Retry(ctx, "record settled offsets", func(callCtx context.Context) error {
		return p.ingest.Begin(callCtx, settlement, written)
	}); err != nil {
		return err
	}
	if err := p.insertCounters(ctx, derived.Points); err != nil {
		return err
	}
	if err := p.insertEndpoints(ctx, derived.Endpoints); err != nil {
		return err
	}
	if err := p.insertEdges(ctx, edges); err != nil {
		return err
	}
	return p.loop.Retry(ctx, "confirm counted offsets", func(callCtx context.Context) error {
		return p.ingest.Complete(callCtx, settlement)
	})
}

// Drain writes everything still held, which is what makes a planned shutdown
// lose nothing. The loop commits it afterwards.
func (p *Pipeline) Drain(ctx context.Context, now time.Time) error {
	p.block.reset()
	p.appendRows(now, p.stages.buffer.Drain(now))
	if err := p.writer.insert(ctx, p.block); err != nil {
		return err
	}
	if err := p.insertSummaries(ctx, p.summaries.DrainAll()); err != nil {
		return err
	}
	// Everything held is drained here, so every held offset is both counted
	// through and written through.
	everything := func(spanStorage) bool { return true }
	reached := ingeststate.RangesOf(p.loop.Reached(everything))
	written := p.ingest.Writing(reached)
	return p.settleDerived(ctx, written, reached,
		p.stages.counters.DrainAll(), p.stages.edges.DrainAll())
}

// insertEdges writes derived service edges, retrying on a store outage.
//
// A lost acknowledgement is what makes a retry dangerous for a summing rollup,
// and the store settles it: `service_edges_1m` carries a deduplication window,
// and the drain sorts its rows so a re-derived block is byte-identical. This
// used to read the table back first as well, which could not tell a retry from
// a minute legitimately reopened by late data, and dropped the late row
// (ADR 0075).
func (p *Pipeline) insertEdges(ctx context.Context, edges []serviceedges.Edge) error {
	if len(edges) == 0 {
		return nil
	}
	return p.loop.Retry(ctx, "insert service edges", func(callCtx context.Context) error {
		return p.edges.Insert(callCtx, edges)
	})
}

// insertCounters writes derived counters, guarded the same way and for the same
// reason: `metrics_sum` has its own deduplication window and the drain sorts.
func (p *Pipeline) insertCounters(ctx context.Context, points []derive.Point) error {
	if len(points) == 0 {
		return nil
	}
	return p.loop.Retry(ctx, "insert counters", func(callCtx context.Context) error {
		return p.metrics.Insert(callCtx, points)
	})
}

// insertEndpoints writes the derived endpoint rollup, guarded the same way and
// for the same reason: `endpoint_stats_1m` carries its own deduplication window
// (000021) and the drain sorts, so a retry after a lost acknowledgement is
// recognised as the repeat it is rather than summed in twice.
func (p *Pipeline) insertEndpoints(ctx context.Context, stats []derive.EndpointStat) error {
	if len(stats) == 0 {
		return nil
	}
	return p.loop.Retry(ctx, "insert endpoint rollup", func(callCtx context.Context) error {
		return p.endpoints.Insert(callCtx, stats)
	})
}

// insertSummaries writes the per-trace rows behind the trace list.
//
// No deduplication guard, unlike the three rollups above, and for a reason
// rather than an omission: every row is a complete snapshot of its trace and
// every column of `trace_summaries` merges with max, min or set union, so a
// retry after a lost acknowledgement writes the same answer again.
func (p *Pipeline) insertSummaries(ctx context.Context, rows []summaryRow) error {
	if len(rows) == 0 {
		return nil
	}
	return p.loop.Retry(ctx, "insert trace summaries", func(callCtx context.Context) error {
		return p.summaryWriter.insert(callCtx, rows)
	})
}

// insertBounded writes the accumulated block under a deadline.
func (p *Pipeline) insertBounded(ctx context.Context) error {
	return p.loop.Retry(ctx, "insert spans", func(callCtx context.Context) error {
		return p.writer.insert(callCtx, p.block)
	})
}

// appendRows queues the released rows for `spans` and folds each into its
// trace's summary. The two see exactly the same rows, which is what makes a
// summary an index of what was stored rather than a claim about what arrived.
func (p *Pipeline) appendRows(now time.Time, rows []spanRow) {
	for _, row := range rows {
		p.block.append(row)
		p.summaries.Observe(now, row)
	}
}

func later(current, candidate time.Time) time.Time {
	if candidate.After(current) {
		return candidate
	}
	return current
}
