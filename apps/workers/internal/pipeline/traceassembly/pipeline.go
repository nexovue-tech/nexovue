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
	"maps"
	"time"

	"github.com/ClickHouse/ch-go/chpool"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dedupe"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/resend"
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
// the healthy ones.
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
	// pending is the rows in block, kept so a refused insert can be narrowed to
	// the rows it refused (refusal.go).
	pending []spanRow

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
	// services writes `service_stats_1m`, anomaly detection's input, from the
	// same inbound spans as the endpoint rollup.
	services *derive.ServiceWriter
	edges    *serviceedges.Writer
	seen     *dedupe.Offsets

	// ingest is the durable half of `seen`: the per-partition record of what has
	// already been counted and what has already been written, which unlike the
	// in-process watermark survives both this process and the store restarting.
	ingest *ingeststate.Guard

	// progress records how far each partition is counted into the derived
	// rollups, which is how the scheduler knows a minute is settled.
	progress *ingeststate.Progress

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
	// the new owner would have written again.
	disownedSpans int

	// disownedCountedSpans and disownedEdgeCalls are the same evidence for the
	// derived rollups: the counts and the calls that left with their partition
	// instead of being summed into `metrics_sum` and `service_edges_1m` here and
	// again by the new owner.
	disownedCountedSpans uint64
	disownedEdgeCalls    uint64

	// spansParked counts spans the store refused, each parked on the dead-letter
	// topic by itself; derivedRowsRefused counts derived rows it refused, which
	// were stepped over. Flat is normal; either climbing names a bad row.
	spansParked        uint64
	derivedRowsRefused uint64

	// servicesOverCap counts service-minutes left out of `service_stats_1m`
	// by the per-organization cap. Flat is normal; climbing names a tenant
	// minting service names.
	servicesOverCap uint64

	// progressHeld is how many partitions' progress is held after a refusal
	// right now; progressHoldsReleased counts holds that reached their bound
	// and were advanced past the refusal anyway. Either above zero means
	// detection is reading "data delayed" or has judged over a hole.
	progressHeld          int
	progressHoldsReleased uint64

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
	// receivedAt is, per partition, when the receiver took the newest record
	// this batch absorbed. The settlement that counts the batch records it
	// with its progress (C3).
	receivedAt map[ingeststate.Partition]time.Time
}

// note folds one absorbed record into what its batch owes storage.
func (s *spanStorage) note(partition ingeststate.Partition, result absorbed) {
	s.latestMinute = later(s.latestMinute, result.latestMinute)
	s.latestEdgeMinute = later(s.latestEdgeMinute, result.latestEdgeMinute)
	if s.receivedAt == nil {
		s.receivedAt = make(map[ingeststate.Partition]time.Time)
	}
	// Records of a partition are absorbed in offset order, so the last one
	// noted is the one at its highest offset.
	s.receivedAt[partition] = result.receivedAt
}

// New builds the pipeline and joins the consumer group. The caller owns the
// ClickHouse pool; Close releases only what New created.
func New(cfg config.Config, pool *chpool.Pool, log *slog.Logger, opts Options) (*Pipeline, error) {
	stages, err := newStages(cfg, opts)
	if err != nil {
		return nil, err
	}

	ingest := ingeststate.NewGuard(ingeststate.NewKeeperMap(pool))
	progress := ingeststate.NewProgress(ingeststate.NewProgressStore(pool), ingeststate.MaxProgressHold)
	pipeline := &Pipeline{
		writer:        newSpanWriter(pool),
		block:         newSpanBlock(),
		summaries:     newSummaries(cfg.Sampling.DecisionTTL, cfg.Sampling.MaxTraces),
		summaryWriter: newSummaryWriter(pool),
		stages:        stages,
		metrics:       derive.NewWriter(pool),
		endpoints:     derive.NewEndpointWriter(pool),
		services:      derive.NewServiceWriter(pool),
		edges:         serviceedges.NewWriter(pool),
		seen:          dedupe.NewOffsets(),
		ingest:        ingest,
		progress:      progress,
		topic:         cfg.Spans.Topic,
		log:           log.With(slog.String("pipeline", string(config.PipelineTraceAssembly))),
		sampling:      cfg.Sampling,
	}

	loop, err := consume.Dial[spanStorage](consume.Settings{
		Name:          string(config.PipelineTraceAssembly),
		Brokers:       cfg.KafkaBrokers,
		Topic:         cfg.Spans.Topic,
		ConsumerGroup: cfg.Spans.ConsumerGroup,
		// A sender's resend is acknowledged and skipped (group 9, C2).
		Resends:     resend.Over(pool, log),
		DLQTopic:    cfg.Spans.DLQTopic,
		MaxMessages: cfg.Spans.MaxMessages,
		MaxDelay:    cfg.Spans.MaxDelay,
		// A partition can arrive from another worker, whose settlements this
		// process never saw. Re-reading the durable state before counting
		// anything is what keeps the handover from counting a minute twice,
		// and from recording its progress behind the previous owner's.
		OnAssigned: func() {
			ingest.Invalidate()
			progress.Invalidate()
		},
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
// taken back while the session is live: the buffered spans, and the shares of
// every open minute those partitions counted.
//
// All of it or none of it. Dropping the buffer alone left the counters
// over-summing; a span row written twice can be collapsed, but a counter summed
// twice has no individual contribution left to remove.
func (p *Pipeline) release(disowned map[string][]int32) {
	partitions := disowned[p.topic]
	if len(partitions) == 0 {
		return
	}

	dropped := p.stages.buffer.Disown(partitions)
	counters := p.stages.counters.Disown(partitions)
	edges := p.stages.edges.Disown(partitions)
	// The in-process replay guard has to forget them at the same instant. Left
	// in place, a partition that came back would have these very records
	// recognised as replays and skipped — and their spans were dropped, not
	// stored, so that is a hole rather than a duplicate.
	p.seen.Forget(p.topic, partitions)
	// A progress hold ending later must not write for a partition another
	// worker now records (C1).
	p.progress.Forget(p.partitionsOf(partitions))
	p.disownedSpans += dropped.Spans
	p.disownedCountedSpans += counters.Spans
	p.disownedEdgeCalls += edges.Calls

	p.log.LogAttrs(context.Background(), slog.LevelInfo, "released what the partitions were holding",
		slog.String("topic", p.topic),
		slog.Any("partitions", partitions),
		slog.Int("spans", dropped.Spans),
		slog.Int("traces", dropped.Traces),
		slog.Uint64("counted_spans", counters.Spans),
		slog.Int("counter_series", counters.Series),
		slog.Int("endpoint_series", counters.Endpoints),
		slog.Int("service_series", counters.Services),
		slog.Uint64("edge_calls", edges.Calls),
		slog.Int("edge_series", edges.Edges),
		slog.Int("edge_pairs_pending", edges.Pending),
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
	p.progress.Invalidate()
}

// partitionsOf names this pipeline's partitions by the topic it consumes.
func (p *Pipeline) partitionsOf(ids []int32) []ingeststate.Partition {
	partitions := make([]ingeststate.Partition, 0, len(ids))
	for _, id := range ids {
		partitions = append(partitions, ingeststate.Partition{Topic: p.topic, ID: id})
	}
	return partitions
}

func samplingOptions(cfg config.Sampling) sample.Options {
	return sample.Options{
		Window:      cfg.Window,
		DecisionTTL: cfg.DecisionTTL,
		MaxTraces:   cfg.MaxTraces,
		MaxSpans:    cfg.MaxSpans,
		MaxBytes:    cfg.MaxBytes,
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
		slog.Int("service_series", p.stages.counters.PendingServices()),
		slog.Uint64("spans_kept", stats.SpansKept),
		slog.Uint64("spans_dropped", stats.SpansDropped),
		slog.Uint64("traces_closed_early", stats.ClosedEarly),
		slog.Uint64("spans_retimed", p.retimedSpans),
		slog.Int("spans_already_stored", p.alreadyStoredSpans),
		slog.Int("spans_disowned", p.disownedSpans),
		slog.Uint64("counted_spans_disowned", p.disownedCountedSpans),
		slog.Uint64("edge_calls_disowned", p.disownedEdgeCalls),
		slog.Uint64("spans_parked", p.spansParked),
		slog.Uint64("derived_rows_refused", p.derivedRowsRefused),
		slog.Uint64("services_over_cap", p.servicesOverCap),
		slog.Int("derived_progress_held", p.progressHeld),
		slog.Uint64("derived_progress_holds_released", p.progressHoldsReleased),
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
	p.startBlock()
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
		produced.note(partition, result)
		skew.merge(result.skew)
		spans += result.spans
		messages++
	}

	if err := p.writeSpans(ctx); err != nil {
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
	p.startBlock()
	p.appendRows(now, p.stages.buffer.Expire(now))
	if err := p.writeSpans(ctx); err != nil {
		return err
	}
	// Before the watermark below claims these spans are stored: a summary lost
	// to a crash after the watermark moved would never be re-derived, and the
	// trace would be missing from the list for as long as its spans are kept.
	if err := p.insertSummaries(ctx, p.summaries.Drain(now)); err != nil {
		return err
	}

	drained := p.derivedDrained(now)
	reached := ingeststate.RangesOf(p.loop.Reached(drained))
	received := receivedThrough(p.loop.ReachedOutput(drained))
	return p.settleDerived(ctx, now, p.spansWritten(now), reached, received,
		p.stages.counters.Drain(now), p.stages.edges.Drain(now))
}

// derivedDrained reports whether everything derived from a batch has been
// drained, without waiting for the sampling window: a batch's spans can still
// be in the buffer while everything derived from them is already exact. Both
// counters and edges must be drained, because both are summing rollups whose
// offsets must not move before they reach storage.
func (p *Pipeline) derivedDrained(now time.Time) func(spanStorage) bool {
	return func(produced spanStorage) bool {
		return p.stages.counters.Drained(now, produced.latestMinute) &&
			p.stages.edges.Drained(now, produced.latestEdgeMinute)
	}
}

// receivedThrough is, per partition, when the newest record of the given
// batches was received. They are in poll order, so a later batch's record is
// the later offset.
func receivedThrough(batches []spanStorage) map[ingeststate.Partition]time.Time {
	received := make(map[ingeststate.Partition]time.Time)
	for _, batch := range batches {
		maps.Copy(received, batch.receivedAt)
	}
	return received
}

// progressMarks pairs each settled partition's last offset with when that
// record was received. A partition none of whose records was absorbed here — a
// batch of resends, or of parked records — has no time to report and is left
// for the next settlement, rather than recorded as received at the epoch.
func progressMarks(
	settlement ingeststate.Settlement,
	received map[ingeststate.Partition]time.Time,
) ingeststate.Marks {
	marks := make(ingeststate.Marks, len(settlement))
	for partition, offsets := range settlement {
		at, known := received[partition]
		if !known {
			continue
		}
		marks[partition] = ingeststate.Mark{CountedThrough: offsets.Max, ReceivedAt: at}
	}
	return marks
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
// The order is the whole point: BEFORE says an insert of this range
// is in flight, AFTER says the store took it, and the Kafka offsets move only
// after that — so a worker that comes back to a restarted store can tell a
// finished insert from an interrupted one instead of guessing.
//
// Progress for detection is recorded last, once the store has acknowledged the
// derived inserts, and only for this settlement's partitions (C1).
//
// The span watermark obeys the same rule in its simpler form. It is written
// after ClickHouse has acknowledged the spans and before the loop commits the
// offsets, in that order and no other: recorded early, a failed insert would
// leave the replay skipping spans nothing ever stored, and a hole cannot be
// repaired where a duplicate row can.
func (p *Pipeline) settleDerived(
	ctx context.Context,
	now time.Time,
	written ingeststate.Written,
	reached map[ingeststate.Partition]ingeststate.Range,
	received map[ingeststate.Partition]time.Time,
	derived derive.Derived,
	edges []serviceedges.Edge,
) error {
	settlement := p.ingest.Advance(reached)
	if err := p.loop.Retry(ctx, "record settled offsets", func(callCtx context.Context) error {
		return p.ingest.Begin(callCtx, settlement, written)
	}); err != nil {
		return err
	}
	refused, err := p.insertDerived(ctx, derived, edges)
	if err != nil {
		return err
	}
	if err := p.loop.Retry(ctx, "confirm counted offsets", func(callCtx context.Context) error {
		return p.ingest.Complete(callCtx, settlement)
	}); err != nil {
		return err
	}
	return p.recordProgress(ctx, now, progressMarks(settlement, received), refused)
}

// insertDerived writes one drain's derived rows, and reports whether the store
// refused any of them. One insert per table carries every partition, so one
// refusal leaves a hole in all of the settlement's partitions (C22).
func (p *Pipeline) insertDerived(
	ctx context.Context,
	derived derive.Derived,
	edges []serviceedges.Edge,
) (bool, error) {
	p.reportServiceCap(ctx, derived.ServicesOverCap)
	inserts := []func(context.Context) (bool, error){
		func(ctx context.Context) (bool, error) { return p.insertCounters(ctx, derived.Points) },
		func(ctx context.Context) (bool, error) { return p.insertEndpoints(ctx, derived.Endpoints) },
		func(ctx context.Context) (bool, error) { return p.insertServices(ctx, derived.Services) },
		func(ctx context.Context) (bool, error) { return p.insertEdges(ctx, edges) },
	}
	anyRefused := false
	for _, insert := range inserts {
		refused, err := insert(ctx)
		if err != nil {
			return false, err
		}
		anyRefused = anyRefused || refused
	}
	return anyRefused, nil
}

// recordProgress writes how far each settled partition is counted, holding it
// after a refusal (ingeststate.Progress), and says when a hold ran out.
func (p *Pipeline) recordProgress(
	ctx context.Context,
	now time.Time,
	marks ingeststate.Marks,
	refused bool,
) error {
	if err := p.loop.Retry(ctx, "read derived progress", p.progress.Refresh); err != nil {
		return err
	}
	var recorded ingeststate.Recorded
	if err := p.loop.Retry(ctx, "record derived progress", func(callCtx context.Context) error {
		var err error
		recorded, err = p.progress.Record(callCtx, now, marks, refused)
		return err
	}); err != nil {
		return err
	}
	p.progressHeld = recorded.Held
	if len(recorded.Released) == 0 {
		return nil
	}
	p.progressHoldsReleased += uint64(len(recorded.Released))
	p.log.ErrorContext(ctx, "derived progress was held past a refusal for its limit; advanced anyway",
		slog.Any("partitions", recorded.Released),
		slog.Duration("held_for", ingeststate.MaxProgressHold),
	)
	return nil
}

// reportServiceCap says once per drain that an organization had more services
// in a minute than the rollup counts, naming the one that lost the most.
func (p *Pipeline) reportServiceCap(ctx context.Context, overCap derive.ServiceCap) {
	if overCap.Services == 0 {
		return
	}
	p.servicesOverCap += uint64(max(overCap.Services, 0)) //nolint:gosec // clamped to non-negative
	p.log.WarnContext(ctx, "left services out of the service rollup; the organization is over its cap",
		slog.Int("services", overCap.Services),
		slog.String("tenant_id", overCap.TenantID),
		slog.Int("cap", derive.MaxServicesPerTenant),
	)
}

// Drain writes everything still held, which is what makes a planned shutdown
// lose nothing. The loop commits it afterwards.
func (p *Pipeline) Drain(ctx context.Context, now time.Time) error {
	p.startBlock()
	p.appendRows(now, p.stages.buffer.Drain(now))
	if err := p.writeSpans(ctx); err != nil {
		return err
	}
	if err := p.insertSummaries(ctx, p.summaries.DrainAll()); err != nil {
		return err
	}
	// Everything held is drained here, so every held offset is both counted
	// through and written through.
	everything := func(spanStorage) bool { return true }
	reached := ingeststate.RangesOf(p.loop.Reached(everything))
	received := receivedThrough(p.loop.ReachedOutput(everything))
	written := p.ingest.Writing(reached)
	return p.settleDerived(ctx, now, written, reached, received,
		p.stages.counters.DrainAll(), p.stages.edges.DrainAll())
}

// insertEdges writes derived service edges, retrying on a store outage.
//
// A lost acknowledgement is what makes a retry dangerous for a summing rollup,
// and the store settles it: `service_edges_1m` carries a deduplication window,
// and the drain sorts its rows so a re-derived block is byte-identical. This
// used to read the table back first as well, which could not tell a retry from
// a minute legitimately reopened by late data, and dropped the late row.
func (p *Pipeline) insertEdges(ctx context.Context, edges []serviceedges.Edge) (bool, error) {
	if len(edges) == 0 {
		return false, nil
	}
	err := p.loop.Retry(ctx, "insert service edges", func(callCtx context.Context) error {
		return p.edges.Insert(callCtx, edges)
	})
	return p.stepOverRefusal(ctx, "service_edges_1m", len(edges), err)
}

// insertCounters writes derived counters, guarded the same way and for the same
// reason: `metrics_sum` has its own deduplication window and the drain sorts.
func (p *Pipeline) insertCounters(ctx context.Context, points []derive.Point) (bool, error) {
	if len(points) == 0 {
		return false, nil
	}
	err := p.loop.Retry(ctx, "insert counters", func(callCtx context.Context) error {
		return p.metrics.Insert(callCtx, points)
	})
	return p.stepOverRefusal(ctx, "metrics_sum", len(points), err)
}

// insertEndpoints writes the derived endpoint rollup, guarded the same way and
// for the same reason: `endpoint_stats_1m` carries its own deduplication window
// (000021) and the drain sorts, so a retry after a lost acknowledgement is
// recognised as the repeat it is rather than summed in twice.
func (p *Pipeline) insertEndpoints(ctx context.Context, stats []derive.EndpointStat) (bool, error) {
	if len(stats) == 0 {
		return false, nil
	}
	err := p.loop.Retry(ctx, "insert endpoint rollup", func(callCtx context.Context) error {
		return p.endpoints.Insert(callCtx, stats)
	})
	return p.stepOverRefusal(ctx, "endpoint_stats_1m", len(stats), err)
}

// insertServices writes the derived service rollup, guarded the same way and
// for the same reason: `service_stats_1m` carries a deduplication window
// (000035) and the drain sorts.
func (p *Pipeline) insertServices(ctx context.Context, stats []derive.ServiceStat) (bool, error) {
	if len(stats) == 0 {
		return false, nil
	}
	err := p.loop.Retry(ctx, "insert service rollup", func(callCtx context.Context) error {
		return p.services.Insert(callCtx, stats)
	})
	return p.stepOverRefusal(ctx, "service_stats_1m", len(stats), err)
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
	err := p.loop.Retry(ctx, "insert trace summaries", func(callCtx context.Context) error {
		return p.summaryWriter.insert(callCtx, rows)
	})
	// Not part of any settlement, so a refusal here holds no progress:
	// detection never reads the trace list.
	_, err = p.stepOverRefusal(ctx, "trace_summaries", len(rows), err)
	return err
}

// startBlock empties the block and the rows it holds, together.
func (p *Pipeline) startBlock() {
	p.block.reset()
	p.pending = p.pending[:0]
}

// appendRows queues the released rows for `spans` and folds each into its
// trace's summary. The two see exactly the same rows, which is what makes a
// summary an index of what was stored rather than a claim about what arrived.
func (p *Pipeline) appendRows(now time.Time, rows []spanRow) {
	for _, row := range rows {
		p.block.append(row)
		p.pending = append(p.pending, row)
		p.summaries.Observe(now, row)
	}
}

func later(current, candidate time.Time) time.Time {
	if candidate.After(current) {
		return candidate
	}
	return current
}
