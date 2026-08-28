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
// is what spanStorage remembers — making delivery at-least-once. Re-inserting
// an identical span row is harmless; re-counting one is not, which is what the
// dedupe stage is for.
type Pipeline struct {
	loop *consume.Loop[spanStorage]

	writer *spanWriter
	block  *spanBlock

	stages  *stages
	metrics *derive.Writer
	edges   *serviceedges.Writer
	seen    *dedupe.Offsets

	// ingest is the durable half of `seen`: the per-partition record of what has
	// already been counted, which unlike the in-process watermark survives both
	// this process and the store restarting (ADR 0061).
	ingest *ingeststate.Guard

	// retimedSpans counts spans whose own timestamp was too far from receive
	// time to bucket on. A number that climbs names a customer with a broken
	// clock, and the warning that recorded each batch says which one.
	retimedSpans uint64

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
	loop, err := consume.Dial[spanStorage](consume.Settings{
		Name:          string(config.PipelineTraceAssembly),
		Brokers:       cfg.KafkaBrokers,
		Topic:         cfg.Spans.Topic,
		ConsumerGroup: cfg.Spans.ConsumerGroup,
		DLQTopic:      cfg.Spans.DLQTopic,
		MaxRows:       cfg.Spans.MaxRows,
		MaxDelay:      cfg.Spans.MaxDelay,
		// A partition can arrive from another worker, whose settlements this
		// process never saw. Re-reading the durable state before counting
		// anything is what keeps the handover from counting a minute twice.
		OnAssigned: ingest.Invalidate,
	}, log)
	if err != nil {
		return nil, err
	}

	return &Pipeline{
		loop:     loop,
		writer:   newSpanWriter(pool),
		block:    newSpanBlock(),
		stages:   stages,
		metrics:  derive.NewWriter(pool),
		edges:    serviceedges.NewWriter(pool),
		seen:     dedupe.NewOffsets(),
		ingest:   ingest,
		log:      log.With(slog.String("pipeline", string(config.PipelineTraceAssembly))),
		sampling: cfg.Sampling,
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
	return []slog.Attr{
		slog.Int("buffered_traces", stats.OpenTraces),
		slog.Int("buffered_spans", stats.BufferedSpans),
		slog.Int("counter_series", p.stages.counters.Pending()),
		slog.Uint64("spans_kept", stats.SpansKept),
		slog.Uint64("spans_dropped", stats.SpansDropped),
		slog.Uint64("traces_closed_early", stats.ClosedEarly),
		slog.Uint64("spans_retimed", p.retimedSpans),
		slog.Int("edge_pairs_pending", edges.PendingSpans),
		slog.Int("edge_series", edges.OpenEdges),
	}
}

// Stored reports whether a batch's spans have left the sampling buffer and its
// counters have reached ClickHouse. Until both, its offsets stay uncommitted.
func (p *Pipeline) Stored(now time.Time, produced spanStorage) bool {
	return !now.Before(produced.spansStoredAt) &&
		p.stages.counters.Drained(now, produced.latestMinute) &&
		p.stages.edges.Drained(now, produced.latestEdgeMinute)
}

// Absorb runs one polled batch through decode, mask, derive and sample, and
// writes whatever the sampler released.
func (p *Pipeline) Absorb(ctx context.Context, polled consume.Polled) (spanStorage, error) {
	counting, err := p.alreadyCounted(ctx, polled.Positions)
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
		counted := firstDelivery && counting.Counts(partition, record.Offset)
		result, err := p.stages.absorbMessage(polled.At, record.Value, counted)
		if err != nil {
			if parkErr := p.loop.Park(ctx, record, err); parkErr != nil {
				return spanStorage{}, parkErr
			}
			continue
		}
		p.appendRows(result.rows)
		produced.latestMinute = later(produced.latestMinute, result.latestMinute)
		skew.merge(result.skew)
		spans += result.spans
		messages++
	}

	if err := p.insertBounded(ctx); err != nil {
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

// alreadyCounted resolves a polled batch against the durable per-partition
// state, so a replay of offsets whose counters are already stored is not
// counted a second time. Its spans still travel — an identical span row is
// idempotent — and only the counters are held back.
func (p *Pipeline) alreadyCounted(
	ctx context.Context,
	positions []consume.Position,
) (ingeststate.Decisions, error) {
	if err := p.loop.Retry(ctx, "read counted offsets", p.ingest.Refresh); err != nil {
		return nil, err
	}

	decisions := p.ingest.Counting(ingeststate.RangesOf(positions))
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

// Settle releases the traces whose window has closed and writes the counters
// whose minute is complete. The loop commits what that made storable.
func (p *Pipeline) Settle(ctx context.Context, now time.Time) error {
	p.block.reset()
	p.appendRows(p.stages.buffer.Expire(now))
	if err := p.insertBounded(ctx); err != nil {
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
	return p.settleDerived(ctx, reached, p.stages.counters.Drain(now), p.stages.edges.Drain(now))
}

// settleCounters writes one drain of counters, bracketed by the durable record
// of the offsets it makes exact.
//
// The order is the whole point (ADR 0061): BEFORE says an insert of this range
// is in flight, AFTER says the store took it, and the Kafka offsets move only
// after that — so a worker that comes back to a restarted store can tell a
// finished insert from an interrupted one instead of guessing.
func (p *Pipeline) settleDerived(
	ctx context.Context,
	reached map[ingeststate.Partition]ingeststate.Range,
	points []derive.Point,
	edges []serviceedges.Edge,
) error {
	settlement := p.ingest.Advance(reached)
	if err := p.loop.Retry(ctx, "record counted offsets", func(callCtx context.Context) error {
		return p.ingest.Begin(callCtx, settlement)
	}); err != nil {
		return err
	}
	if err := p.insertCounters(ctx, points); err != nil {
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
	p.appendRows(p.stages.buffer.Drain(now))
	if err := p.writer.insert(ctx, p.block); err != nil {
		return err
	}
	// Everything held is drained here, so every held offset is counted through.
	reached := ingeststate.RangesOf(p.loop.Reached(func(spanStorage) bool { return true }))
	return p.settleDerived(ctx, reached, p.stages.counters.DrainAll(), p.stages.edges.DrainAll())
}

// insertCounters writes derived counters, retrying without ever writing twice:
// a block is atomic, so its first point answers for the batch.
// insertEdges writes derived service edges the same way counters are written:
// a block is atomic, so its first edge answers for the batch, and the read
// before the retry is what stops a lost acknowledgement becoming a double count
// (ADR 0061).
func (p *Pipeline) insertEdges(ctx context.Context, edges []serviceedges.Edge) error {
	if len(edges) == 0 {
		return nil
	}
	return p.loop.WriteOnce(ctx, "insert service edges",
		func(callCtx context.Context) (bool, error) {
			return p.edges.Applied(callCtx, edges[0])
		},
		func(callCtx context.Context) error {
			return p.edges.Insert(callCtx, edges)
		})
}

func (p *Pipeline) insertCounters(ctx context.Context, points []derive.Point) error {
	if len(points) == 0 {
		return nil
	}
	return p.loop.WriteOnce(ctx, "insert counters",
		func(callCtx context.Context) (bool, error) {
			return p.metrics.Applied(callCtx, points[0])
		},
		func(callCtx context.Context) error {
			return p.metrics.Insert(callCtx, points)
		})
}

// insertBounded writes the accumulated block under a deadline.
func (p *Pipeline) insertBounded(ctx context.Context) error {
	return p.loop.Retry(ctx, "insert spans", func(callCtx context.Context) error {
		return p.writer.insert(callCtx, p.block)
	})
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
