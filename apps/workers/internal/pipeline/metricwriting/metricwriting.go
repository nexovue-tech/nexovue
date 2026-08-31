// Package metricwriting runs the metric-ingest pipeline: it consumes the OTLP
// metric envelopes customers send, and writes each point to the table its shape
// belongs in (ADR 0066).
//
// It converts nothing. Aggregation temporality, start timestamp, monotonicity
// and the no-recorded-value flag are stored exactly as they arrived, because
// none of them can be recovered afterwards and turning a cumulative counter
// into a delta needs reset detection this stage cannot do correctly. The
// arithmetic belongs to the read side, which can see a whole series.
//
// Only the stages are here. The machinery around them — the consume loop, the
// offsets, the retry budget, dead-letter parking and the heartbeat — is
// internal/pipeline/consume, which every signal's pipeline shares.
package metricwriting

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
	"github.com/twmb/franz-go/pkg/kgo"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

// AttributeMasker rewrites the attributes of a decoded OTLP metrics payload in
// place, so no stage after it can see the original values.
//
// Narrow on purpose, like the trace pipeline's sources: this stage needs an
// answer, not the cache. `mask.Masker` does not implement it yet — it masks
// spans only — and until it does, this pipeline has nothing to run the metric
// payload through. New refuses to build without one rather than quietly writing
// unmasked customer attributes to the store.
type AttributeMasker interface {
	MaskResourceMetrics(projectID string, batches []*metricspb.ResourceMetrics) int
}

// RetentionSource answers how long one project's data is kept.
type RetentionSource interface {
	Retention(projectID string) (raw, derived uint16)
}

// Options are the collaborators the pipeline does not own.
type Options struct {
	// Masking is required: the pipeline order invariant is that an unmasked
	// value never reaches the store.
	Masking AttributeMasker

	// Retention is optional. Without it every row is stamped with the
	// deployment default, which is what a project the refresher has not seen
	// gets anyway.
	Retention RetentionSource
}

// staticRetention answers the same for every project — the deployment default
// when nothing has told the worker otherwise.
type staticRetention struct{ raw, derived uint16 }

func (s staticRetention) Retention(string) (uint16, uint16) { return s.raw, s.derived }

// Pipeline is the metric half of consume → decode → transform → write →
// commit-when-stored.
//
// It holds nothing between batches: every point is written where it is decoded,
// so a batch's rows have reached storage by the time Absorb returns and its
// offsets may move immediately. That is why Stored is unconditionally true and
// Settle and Drain have nothing to do.
//
// Moving immediately is still not moving atomically. Between the inserts being
// acknowledged and the offsets being committed the batch is stored and still
// replayable, so an abrupt exit in that window writes its points again. How far
// each partition is written is kept in Keeper for that reason (migration
// 000029), in the same table the log pipeline uses.
type Pipeline struct {
	loop   *consume.Loop[stored]
	writer *writer

	// storedThrough is how far each partition's rows are in the store, kept in
	// Keeper so it survives the restart that causes the replay.
	storedThrough *ingeststate.WriteGuard

	gauge        *gaugeBlock
	sum          *sumBlock
	histogram    *histogramBlock
	expHistogram *expHistogramBlock
	summary      *summaryBlock

	masker    AttributeMasker
	retention RetentionSource

	// Running totals for the heartbeat. A parked count that climbs names a
	// customer sending something we cannot store.
	written uint64
	parked  uint64
	masked  uint64
	// alreadyStored counts messages a replay offered whose rows the store had
	// already acknowledged.
	alreadyStored uint64

	// maxRows caps how many rows are held across the five blocks before they
	// are inserted, and with it the memory one batch costs. The batch's message
	// bound cannot: a message carries as many data points as the sender's
	// exporter batched.
	maxRows int

	log *slog.Logger
}

// stored is what a batch owes storage once Absorb returns: nothing. The rows
// are already in ClickHouse, so there is no state to remember them by.
type stored struct{}

// New builds the pipeline and joins the consumer group. The caller owns the
// ClickHouse pool; Close releases only what New created.
func New(cfg config.Config, pool *chpool.Pool, log *slog.Logger, opts Options) (*Pipeline, error) {
	if opts.Masking == nil {
		return nil, errors.New(
			"metric-ingest needs a masker: metric attributes carry the same customer " +
				"data span attributes do, and an unmasked value must never reach the store")
	}

	guard := ingeststate.NewWriteGuard(ingeststate.NewIngestWatermarks(pool))
	loop, err := consume.Dial[stored](consume.Settings{
		Name:          string(config.PipelineMetricIngest),
		Brokers:       cfg.KafkaBrokers,
		Topic:         cfg.MetricIngest.Topic,
		ConsumerGroup: cfg.MetricIngest.ConsumerGroup,
		DLQTopic:      cfg.MetricIngest.DLQTopic,
		// consume's MaxRows has always counted messages; see MetricIngest.
		MaxRows:  cfg.MetricIngest.MaxMessages,
		MaxDelay: cfg.MetricIngest.MaxDelay,
		// A partition can arrive from another worker, whose watermark this
		// process never read.
		OnAssigned: guard.Invalidate,
	}, log)
	if err != nil {
		return nil, err
	}

	return &Pipeline{
		loop:          loop,
		writer:        newWriter(pool),
		storedThrough: guard,
		gauge:         newGaugeBlock(),
		sum:           newSumBlock(),
		histogram:     newHistogramBlock(),
		expHistogram:  newExpHistogramBlock(),
		summary:       newSummaryBlock(),
		masker:        opts.Masking,
		retention:     retentionSource(cfg, opts),
		maxRows:       cfg.MetricIngest.MaxRows,
		log:           log.With(slog.String("pipeline", string(config.PipelineMetricIngest))),
	}, nil
}

func retentionSource(cfg config.Config, opts Options) RetentionSource {
	if opts.Retention != nil {
		return opts.Retention
	}
	return staticRetention{raw: cfg.Retention.RawDays, derived: cfg.Retention.DerivedDays}
}

// Close leaves the consumer group and releases the broker clients.
func (p *Pipeline) Close() { p.loop.Close() }

// Run consumes until ctx is cancelled.
func (p *Pipeline) Run(ctx context.Context) error { return p.loop.Run(ctx, p) }

// Running reports whether the consume loop is live, for the readiness probe.
func (p *Pipeline) Running(ctx context.Context) error { return p.loop.Running(ctx) }

// Describe is what the startup line says about this pipeline beyond its topic
// and batch limits.
func (p *Pipeline) Describe() []slog.Attr {
	return []slog.Attr{
		slog.String("metric_tables", gaugeTable+", "+sumTable+", "+histogramTable+
			", "+expHistogramTable+", "+summaryTable),
		// The loop's own batch_max_rows counts messages; this is rows.
		slog.Int("insert_max_rows", p.maxRows),
	}
}

// Holding is what the heartbeat says. Nothing is buffered, so these are running
// totals rather than depths: parked climbing is the one that names a problem.
func (p *Pipeline) Holding() []slog.Attr {
	return []slog.Attr{
		slog.Uint64("metric_points_written", p.written),
		slog.Uint64("messages_already_stored", p.alreadyStored),
		slog.Uint64("messages_parked", p.parked),
		slog.Uint64("values_masked", p.masked),
	}
}

// Stored reports that a batch's rows have reached storage. Always true: Absorb
// writes them before it returns, so a batch that got that far owes nothing.
func (p *Pipeline) Stored(time.Time, stored) bool { return true }

// Settle has nothing to write: this pipeline holds nothing between batches.
func (p *Pipeline) Settle(context.Context, time.Time) error { return nil }

// Drain has nothing to write, for the same reason.
func (p *Pipeline) Drain(context.Context, time.Time) error { return nil }

// Absorb decodes one polled batch, masks it, flattens it into the five tables
// and writes them.
//
// The five inserts are not one transaction — ClickHouse has none across tables.
// What holds instead is that they all happen inside this call, before any offset
// moves, so a failure re-consumes the whole batch. The cost is that a permanent
// failure partway through leaves the earlier tables holding rows the redelivery
// writes again: a metric row is not unique, and a reader that sums one must
// group by series and timestamp first.
//
// The batch is written in chunks of at most maxRows across the five tables.
// A message carries as many data points as the sender batched, so the batch's
// message bound says nothing about how much is held.
func (p *Pipeline) Absorb(ctx context.Context, polled consume.Polled) (stored, error) {
	if err := p.loop.Retry(ctx, "read written offsets", p.storedThrough.Refresh); err != nil {
		return stored{}, err
	}

	p.resetBlocks()
	messages, points := 0, 0
	for at, record := range polled.Records {
		// A record at or below the watermark had its rows acknowledged by the
		// store before this process died, and is skipped whole. It cannot need
		// parking either: a message that failed to decode was parked before its
		// offset could be covered.
		if !p.storing(record) {
			p.alreadyStored++
			continue
		}
		batch, err := p.absorbMessage(polled.At, record.Value)
		if err != nil {
			if parkErr := p.loop.Park(ctx, record, err); parkErr != nil {
				return stored{}, parkErr
			}
			p.parked++
			continue
		}
		p.appendRows(batch)
		messages++

		if p.blockRows() < p.maxRows {
			continue
		}
		inserted, err := p.writeChunk(ctx, polled.Positions[:at+1])
		if err != nil {
			return stored{}, err
		}
		points += inserted
	}

	inserted, err := p.writeChunk(ctx, polled.Positions)
	if err != nil {
		return stored{}, err
	}
	points += inserted

	p.log.DebugContext(ctx, "batch absorbed",
		slog.Int("messages", messages),
		slog.Int("metric_points", points),
	)
	return stored{}, nil
}

// writeChunk inserts what has accumulated across the five blocks, then records
// how far the batch's offsets are written. It reports how many rows the store
// took. The blocks are emptied only on success: a failure ends the pipeline,
// and the batch is re-consumed from the broker.
//
// The order is the substance. The watermark is written only once ClickHouse has
// acknowledged all five inserts — never before them. A watermark claimed early
// would make a replay skip rows nothing ever wrote, and "no data loss between
// receiver and storage" is a phase-1 invariant while duplication is not. After
// the acknowledgement the worst a lost write costs is the duplicate this
// exists to prevent.
//
// It is also what closes the five-table gap named above: the tables are written
// in a fixed order inside one call, so a batch whose gauge rows landed and
// whose histogram rows did not has no watermark at all and is replayed whole.
func (p *Pipeline) writeChunk(ctx context.Context, covered []consume.Position) (int, error) {
	rows := p.blockRows()
	if err := p.insertBlocks(ctx); err != nil {
		return 0, err
	}
	p.resetBlocks()
	p.written += countOf(rows)

	if len(covered) == 0 {
		return rows, nil
	}
	return rows, p.loop.Retry(ctx, "record written offsets", func(callCtx context.Context) error {
		return p.storedThrough.Advance(callCtx, covered)
	})
}

// storing reports whether this record's rows still have to be written.
func (p *Pipeline) storing(record *kgo.Record) bool {
	return p.storedThrough.Storing(
		ingeststate.Partition{Topic: record.Topic, ID: record.Partition}, record.Offset)
}

// absorbMessage runs one Kafka message through decode → mask → flatten. A
// failure at any step is a *consume.Rejection naming the tenant it belonged to.
func (p *Pipeline) absorbMessage(now time.Time, message []byte) (rows, error) {
	payload, err := decodeEnvelope(message)
	if err != nil {
		return rows{}, err
	}

	// Mask first: from here on no stage can see the original values.
	projectID := payload.envelope.GetProjectId()
	p.masked += countOf(p.masker.MaskResourceMetrics(projectID, payload.request.GetResourceMetrics()))

	_, derivedDays := p.retention.Retention(projectID)
	return payload.flatten(stamps{
		receivedAt:    consume.ReceivedAt(payload.envelope, now),
		retentionDays: derivedDays,
	})
}

func (p *Pipeline) appendRows(batch rows) {
	for _, row := range batch.gauge {
		p.gauge.append(row)
	}
	for _, row := range batch.sum {
		p.sum.append(row)
	}
	for _, row := range batch.histogram {
		p.histogram.append(row)
	}
	for _, row := range batch.expHistogram {
		p.expHistogram.append(row)
	}
	for _, row := range batch.summary {
		p.summary.append(row)
	}
}

func (p *Pipeline) resetBlocks() {
	p.gauge.reset()
	p.sum.reset()
	p.histogram.reset()
	p.expHistogram.reset()
	p.summary.reset()
}

func (p *Pipeline) blockRows() int {
	return p.gauge.rows() + p.sum.rows() + p.histogram.rows() +
		p.expHistogram.rows() + p.summary.rows()
}

// pending is one table's share of a batch, ready to insert.
type pending struct {
	table   string
	rows    int
	columns proto.Input
}

// insertBlocks writes every non-empty block under the loop's retry budget. An
// empty block is not sent at all, so a metric with no data points leaves no row
// rather than a row of zeros.
//
// The order is fixed so a partial failure always leaves the same prefix
// written, which makes the state after one recognisable rather than arbitrary.
func (p *Pipeline) insertBlocks(ctx context.Context) error {
	blocks := []pending{
		{gaugeTable, p.gauge.rows(), p.gauge.columns},
		{sumTable, p.sum.rows(), p.sum.columns},
		{histogramTable, p.histogram.rows(), p.histogram.columns},
		{expHistogramTable, p.expHistogram.rows(), p.expHistogram.columns},
		{summaryTable, p.summary.rows(), p.summary.columns},
	}
	for _, block := range blocks {
		if block.rows == 0 {
			continue
		}
		if err := p.loop.Retry(ctx, "insert "+block.table, func(callCtx context.Context) error {
			return p.writer.insert(callCtx, block.table, block.rows, block.columns)
		}); err != nil {
			return err
		}
	}
	return nil
}

// countOf widens a count that cannot be negative. Row and mask counts come back
// as int from the packages that produce them, and the running totals are uint64
// because that is what slog logs them as; a negative would mean the producer is
// broken, so it reads as zero rather than wrapping to an enormous number.
func countOf(n int) uint64 {
	if n < 0 {
		return 0
	}
	return uint64(n)
}
