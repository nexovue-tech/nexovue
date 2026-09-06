// Package logwriting runs the log-writing pipeline: it consumes log envelopes
// from Kafka, masks them, flattens them to rows and writes them to ClickHouse.
//
// Only the stages are here. The machinery around them — the consume loop, the
// offsets, the retry budget, dead-letter parking and the heartbeat — is
// internal/pipeline/consume, which every signal's pipeline shares.
//
// Unlike trace assembly, nothing is buffered: a log record has no trace to wait
// for and no sampling decision to make, so a batch is written where it is
// decoded and its offsets may move as soon as the insert returns. That is what
// makes the batch's storage receipt an empty struct.
//
// "As soon as" is not "at the same time", and the gap is the whole reason this
// pipeline needs a durable watermark. Between the insert being acknowledged and
// the offsets being committed the batch is already stored and still replayable,
// so an abrupt exit in that window writes its rows a second time — `logs` is a
// plain MergeTree and nothing deduplicates it on read. internal/pipeline/
// ingeststate keeps how far each partition is written in Keeper, so the replay
// after a restart is recognised (migration 000029).
package logwriting

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/ClickHouse/ch-go/chpool"
	"github.com/twmb/franz-go/pkg/kgo"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/vitals"
)

// Masker rewrites the decoded payload in place, so that no stage after it can
// see an original value. In place is the point: the pipeline holds the only
// copy, and masking the payload rather than the rows is what makes an unmasked
// value unable to reach the store.
//
// Narrow on purpose — the stages need the call, not the rule cache behind it.
type Masker interface {
	// MaskResourceLogs masks every value the project's rules cover and returns
	// how many it replaced, which is what the pipeline logs.
	MaskResourceLogs(projectID string, batches []*logspb.ResourceLogs) int
}

// RetentionSource answers how long one project's data is kept. Its two results
// are raw and derived days; logs are raw, and the second is ignored here so
// that one cache satisfies this and the trace pipeline alike.
type RetentionSource interface {
	Retention(projectID string) (raw, derived uint16)
}

// staticRetention answers the same for every project. The deployment default
// when nothing has told the worker otherwise.
type staticRetention struct{ raw uint16 }

func (s staticRetention) Retention(string) (uint16, uint16) { return s.raw, 0 }

// Options are the collaborators the pipeline does not own.
type Options struct {
	// Masking is required. A pipeline with no masker would write customer
	// values to a store that has no way to take them back, so it refuses to
	// start rather than running unmasked — see the mask-first invariant in
	// apps/workers/CLAUDE.md.
	Masking Masker

	// Retention is optional: without it every project is stamped with the
	// deployment default.
	Retention RetentionSource
}

// Pipeline is the log half of consume → decode → transform → write →
// commit-when-stored. It runs log envelopes through decode → mask → sink, and
// the loop around it moves the offsets.
type Pipeline struct {
	loop *consume.Loop[written]

	writer *logWriter
	block  *logBlock

	// The browser door's web vitals ride this pipeline: they arrive as ordinary
	// log records and are written to `logs` like any other, then promoted into
	// their own table and rollup. Held here so they flush on the same chunk
	// boundaries as the rows they came from — see writeChunk.
	vitalWriter   vitalPromoter
	vitalReadings []vitals.Reading
	vitalRollup   *vitals.Rollup

	// written is how far each partition's rows are in the store, kept in Keeper
	// so it survives the restart that causes the replay.
	written *ingeststate.WriteGuard

	masker    Masker
	retention RetentionSource

	// Running totals for the heartbeat: what this process has written since it
	// started, and how many values the mask stage replaced on the way.
	logsWritten uint64
	// parked is DLQ depth for this signal, and a privacy signal too: a parked
	// message failed to decode and so bypassed masking entirely.
	parked       uint64
	valuesMasked uint64
	// vitalsWritten is how many web vital readings were promoted out of those
	// logs. It reads zero on a deployment with no browser door installed.
	vitalsWritten uint64
	// vitalsRefused counts readings the store would not take. Non-zero means a
	// RUM screen is missing data that `logs` still holds — recoverable, and
	// invisible without this number.
	vitalsRefused uint64
	// alreadyStored counts messages a replay offered whose rows the store had
	// already acknowledged. It is the direct evidence that the watermark is
	// doing something, and reads zero on a worker that never restarted.
	alreadyStored uint64

	defaultRetentionDays uint16

	// maxRows caps one insert, and with it the rows held in memory. The batch's
	// message bound cannot: a message carries as many log records as the
	// sender's exporter batched.
	maxRows int

	log *slog.Logger
}

// vitalPromoter is the vitals writer's shape as this pipeline uses it. Narrow
// and declared here so a test can stand in a store that refuses, which is the
// path that decides whether one browser record can cost every other tenant in
// the chunk its log records.
type vitalPromoter interface {
	Insert(ctx context.Context, readings []vitals.Reading, stats []vitals.Stat) error
}

// written is what one batch still owes storage: nothing. The insert happens
// inside Absorb, so by the time the loop asks, the rows are already in
// ClickHouse and the batch's offsets are free to move.
type written struct{}

// New builds the pipeline and joins the consumer group. The caller owns the
// ClickHouse pool; Close releases only what New created.
func New(cfg config.Config, pool *chpool.Pool, log *slog.Logger, opts Options) (*Pipeline, error) {
	if opts.Masking == nil {
		return nil, errors.New("log-writing pipeline: a masker is required")
	}

	guard := ingeststate.NewWriteGuard(ingeststate.NewIngestWatermarks(pool))
	loop, err := consume.Dial[written](consume.Settings{
		Name:          string(config.PipelineLogWriting),
		Brokers:       cfg.KafkaBrokers,
		Topic:         cfg.Logs.Topic,
		ConsumerGroup: cfg.Logs.ConsumerGroup,
		DLQTopic:      cfg.Logs.DLQTopic,
		MaxMessages:   cfg.Logs.MaxMessages,
		MaxDelay:      cfg.Logs.MaxDelay,
		// A partition can arrive from another worker, whose watermark this
		// process never read. Re-reading before writing anything is what keeps
		// the handover from writing a partition's rows twice.
		OnAssigned: guard.Invalidate,
	}, log)
	if err != nil {
		return nil, err
	}

	return &Pipeline{
		loop:                 loop,
		writer:               newLogWriter(pool),
		block:                newLogBlock(),
		vitalWriter:          vitals.NewWriter(pool),
		vitalRollup:          vitals.NewRollup(),
		written:              guard,
		masker:               opts.Masking,
		retention:            retentionSource(cfg, opts),
		defaultRetentionDays: cfg.Retention.RawDays,
		maxRows:              cfg.Logs.MaxRows,
		log:                  log.With(slog.String("pipeline", string(config.PipelineLogWriting))),
	}, nil
}

func retentionSource(cfg config.Config, opts Options) RetentionSource {
	if opts.Retention != nil {
		return opts.Retention
	}
	return staticRetention{raw: cfg.Retention.RawDays}
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
		slog.Uint64("default_retention_days", uint64(p.defaultRetentionDays)),
		// The loop's own batch_max_messages counts messages; this is rows.
		slog.Int("insert_max_rows", p.maxRows),
	}
}

// Holding is what the heartbeat says about this pipeline. It holds nothing
// between turns, so the numbers are throughput rather than depth; the one that
// shows a wedge is the loop's own commit lag.
func (p *Pipeline) Holding() []slog.Attr {
	return []slog.Attr{
		slog.Uint64("logs_written", p.logsWritten),
		slog.Uint64("web_vitals_written", p.vitalsWritten),
		slog.Uint64("web_vitals_refused", p.vitalsRefused),
		slog.Uint64("messages_already_stored", p.alreadyStored),
		slog.Uint64("messages_parked", p.parked),
		slog.Uint64("values_masked", p.valuesMasked),
	}
}

// Stored is always true: Absorb writes the batch before it returns, so nothing
// a batch produced is still owed to storage.
func (p *Pipeline) Stored(time.Time, written) bool { return true }

// Absorb decodes one polled batch, masks it, flattens it to rows and writes
// them. A message it cannot decode is parked before its offset can move.
//
// The batch is written in chunks of at most maxRows rather than in one insert.
// A message carries as many log records as the sender batched, so the batch's
// message bound says nothing about how much is held; unbounded, 5000 messages
// held 50,000 rows and the worker was OOM-killed.
func (p *Pipeline) Absorb(ctx context.Context, polled consume.Polled) (written, error) {
	if err := p.loop.Retry(ctx, "read written offsets", p.written.Refresh); err != nil {
		return written{}, err
	}

	p.block.reset()
	p.resetVitals()
	messages, masked, stored := 0, 0, 0
	// inBlock is exactly the records whose rows are in the block, which is what
	// has to be parked if the store refuses it. Skipped and parked records are
	// deliberately not in it.
	var inBlock []*kgo.Record

	for at, record := range polled.Records {
		// A record at or below the watermark had its rows acknowledged by the
		// store before this process died, and it is skipped whole rather than
		// re-flattened. It cannot need parking either: a message that failed to
		// decode was parked before its offset could be covered.
		if !p.storing(record) {
			p.alreadyStored++
			continue
		}
		rows, readings, maskedHere, err := p.absorbMessage(polled.At, record.Value)
		if err != nil {
			if parkErr := p.loop.Park(ctx, record, err); parkErr != nil {
				return written{}, parkErr
			}
			p.parked++
			continue
		}
		for _, row := range rows {
			p.block.append(row)
		}
		p.holdVitals(readings)
		inBlock = append(inBlock, record)
		masked += maskedHere
		messages++

		if p.block.rows() < p.maxRows {
			continue
		}
		// Flushed at a message boundary, so every record up to and including
		// this one is finished with, whichever way it went.
		inserted, err := p.writeChunk(ctx, inBlock, polled.Positions[:at+1])
		if err != nil {
			return written{}, err
		}
		stored += inserted
		inBlock = inBlock[:0]
	}

	inserted, err := p.writeChunk(ctx, inBlock, polled.Positions)
	if err != nil {
		return written{}, err
	}
	stored += inserted
	p.valuesMasked += countOf(masked)

	p.log.DebugContext(ctx, "batch absorbed",
		slog.Int("messages", messages),
		slog.Int("logs", stored),
		slog.Int("values_masked", masked),
	)
	return written{}, nil
}

// writeChunk inserts what has accumulated in the block, then records how far
// the batch's offsets are written. It reports how many rows the store took.
//
// The order is the substance. The watermark is written only once ClickHouse has
// acknowledged the insert — never before it. A watermark claimed early would
// make a replay skip rows nothing ever wrote, and "no data loss between
// receiver and storage" is a phase-1 invariant while duplication is not. After
// the acknowledgement the failure modes are: lose the watermark write and the
// next replay duplicates, which is exactly today's behaviour and recoverable;
// there is no ordering in which a row goes missing.
//
// The Kafka commit is later still, in the loop, so the watermark is always at
// or ahead of the committed offsets — and that gap is the replay this skips.
func (p *Pipeline) writeChunk(
	ctx context.Context,
	inBlock []*kgo.Record,
	covered []consume.Position,
) (int, error) {
	rows := p.block.rows()
	// The vitals go in first. If the log insert then fails, the batch is retried
	// whole and the readings that already landed are recognised as the repeat
	// they are by their deduplication token; the reverse order would leave a
	// reading promoted from a row that no retry ever wrote.
	promoted, err := p.promoteVitals(ctx)
	if err == nil {
		err = p.insertBounded(ctx)
	}
	p.block.reset()
	p.resetVitals()

	stored := 0
	switch {
	case err == nil:
		p.logsWritten += countOf(rows)
		p.vitalsWritten += countOf(promoted)
		stored = rows
	case !consume.IsPermanent(err):
		return 0, err
	default:
		// ClickHouse refused these rows and will refuse them again, so the
		// partition can never advance past them on its own. Park them: the
		// records are kept on the dead-letter topic rather than dropped, and
		// every other tenant's ingest keeps running. Before this, one malformed
		// record stopped every signal for every tenant for an hour.
		//
		// The watermark still advances over them. Parked is not stored, but it
		// is just as finished: a replay that re-inserted them would be refused
		// again and park a second copy on the dead-letter topic.
		if parkErr := p.parkRefusedChunk(ctx, inBlock, err); parkErr != nil {
			return 0, parkErr
		}
	}
	return stored, p.advanceWritten(ctx, covered)
}

// advanceWritten records how far this batch's offsets are written, under the
// loop's retry budget so a brief Keeper outage costs a retry rather than the
// process.
func (p *Pipeline) advanceWritten(ctx context.Context, covered []consume.Position) error {
	if len(covered) == 0 {
		return nil
	}
	return p.loop.Retry(ctx, "record written offsets", func(callCtx context.Context) error {
		return p.written.Advance(callCtx, covered)
	})
}

// storing reports whether this record's rows still have to be written.
func (p *Pipeline) storing(record *kgo.Record) bool {
	return p.written.Storing(
		ingeststate.Partition{Topic: record.Topic, ID: record.Partition}, record.Offset)
}

// absorbMessage runs one Kafka message through decode → mask → flatten.
func (p *Pipeline) absorbMessage(
	now time.Time,
	message []byte,
) ([]logRow, []vitals.Reading, int, error) {
	payload, err := decodeEnvelope(message)
	if err != nil {
		return nil, nil, 0, err
	}

	projectID := payload.envelope.GetProjectId()
	// Mask first: from here on no stage can see the original values. The vital
	// readings are promoted after this, so they inherit the masking rather than
	// needing their own.
	masked := p.masker.MaskResourceLogs(projectID, payload.request.GetResourceLogs())

	rawDays, derivedDays := p.retention.Retention(projectID)
	marks := stamps{
		receivedAt:           consume.ReceivedAt(payload.envelope, now),
		retentionDays:        rawDays,
		derivedRetentionDays: derivedDays,
	}
	rows, readings := payload.rows(marks)
	return rows, readings, masked, nil
}

// Settle has nothing to do: every batch is written inside Absorb, so nothing
// falls due between turns of the loop.
func (p *Pipeline) Settle(context.Context, time.Time) error { return nil }

// Drain has nothing to write for the same reason. A batch polled but never
// absorbed is simply not committed, and the next owner of the partition reads
// it again.
func (p *Pipeline) Drain(context.Context, time.Time) error { return nil }

// parkRefusedChunk moves aside every record whose rows were in the insert the
// store refused.
//
// The chunk, not the offending record: the insert reports which *rows* it
// rejected only by message, and one bad record is carried in a block with up to
// several thousand good ones. Parking all of them keeps every record — the DLQ
// topic holds them for reprocessing — and is what lets the offset move.
func (p *Pipeline) parkRefusedChunk(ctx context.Context, covered []*kgo.Record, cause error) error {
	for _, record := range covered {
		if err := p.loop.Park(ctx, record, cause); err != nil {
			return err
		}
		p.parked++
	}
	p.log.ErrorContext(ctx, "the store refused an insert; parked its records rather than retried",
		slog.Int("messages", len(covered)),
		slog.String("error", cause.Error()),
	)
	return nil
}

// holdVitals keeps one message's readings until the chunk they belong to is
// written, folding each into the per-minute rollup as it arrives.
func (p *Pipeline) holdVitals(readings []vitals.Reading) {
	for _, reading := range readings {
		p.vitalReadings = append(p.vitalReadings, reading)
		p.vitalRollup.Add(reading)
	}
}

// resetVitals drops what the last chunk held, keeping the slice's capacity.
func (p *Pipeline) resetVitals() {
	p.vitalReadings = p.vitalReadings[:0]
	p.vitalRollup.Reset()
}

// promoteVitals writes the readings promoted out of this chunk, and the rollup
// rows they fold into, under the same retry budget as the logs. It reports how
// many readings the store took.
//
// **A refusal here does not park the chunk.** The readings are a promoted copy
// of records that are about to be written to `logs`, and migration 000030 says
// every row of `web_vitals` is re-derivable from them — so a browser record the
// store will not take costs a screen, where parking the chunk would cost every
// other tenant in it their log records. That distinction is the whole reason
// the two inserts are separate: one malformed record once stopped ingest for
// every tenant for 41 minutes, and this is the same shape.
//
// A transient failure is still returned, because that one is worth retrying.
func (p *Pipeline) promoteVitals(ctx context.Context) (int, error) {
	if len(p.vitalReadings) == 0 {
		return 0, nil
	}
	rows := p.vitalRollup.Rows()
	err := p.loop.Retry(ctx, "insert web vitals", func(callCtx context.Context) error {
		return p.vitalWriter.Insert(callCtx, p.vitalReadings, rows)
	})
	return p.afterPromoting(ctx, err)
}

// afterPromoting turns one vitals insert's outcome into what it costs: rows
// stored, an error worth retrying, or a refusal counted and stepped over.
//
// Separate from the insert because it is the decision, and the decision is what
// has to be provable without a broker behind it.
func (p *Pipeline) afterPromoting(ctx context.Context, err error) (int, error) {
	switch {
	case err == nil:
		return len(p.vitalReadings), nil
	case !consume.IsPermanent(err):
		return 0, err
	default:
		p.vitalsRefused += countOf(len(p.vitalReadings))
		p.log.ErrorContext(ctx, "the store refused these web vitals; the log records they "+
			"were promoted from are still being written",
			slog.Int("readings", len(p.vitalReadings)),
			slog.String("error", err.Error()),
		)
		return 0, nil
	}
}

// insertBounded writes the accumulated block under a deadline.
func (p *Pipeline) insertBounded(ctx context.Context) error {
	return p.loop.Retry(ctx, "insert logs", func(callCtx context.Context) error {
		return p.writer.insert(callCtx, p.block)
	})
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
