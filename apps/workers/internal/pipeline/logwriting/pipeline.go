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
package logwriting

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/ClickHouse/ch-go/chpool"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
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

	masker    Masker
	retention RetentionSource

	// Running totals for the heartbeat: what this process has written since it
	// started, and how many values the mask stage replaced on the way.
	logsWritten  uint64
	valuesMasked uint64

	defaultRetentionDays uint16
	log                  *slog.Logger
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

	loop, err := consume.Dial[written](consume.Settings{
		Name:          string(config.PipelineLogWriting),
		Brokers:       cfg.KafkaBrokers,
		Topic:         cfg.Logs.Topic,
		ConsumerGroup: cfg.Logs.ConsumerGroup,
		DLQTopic:      cfg.Logs.DLQTopic,
		MaxRows:       cfg.Logs.MaxRows,
		MaxDelay:      cfg.Logs.MaxDelay,
	}, log)
	if err != nil {
		return nil, err
	}

	return &Pipeline{
		loop:                 loop,
		writer:               newLogWriter(pool),
		block:                newLogBlock(),
		masker:               opts.Masking,
		retention:            retentionSource(cfg, opts),
		defaultRetentionDays: cfg.Retention.RawDays,
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
	}
}

// Holding is what the heartbeat says about this pipeline. It holds nothing
// between turns, so the numbers are throughput rather than depth; the one that
// shows a wedge is the loop's own commit lag.
func (p *Pipeline) Holding() []slog.Attr {
	return []slog.Attr{
		slog.Uint64("logs_written", p.logsWritten),
		slog.Uint64("values_masked", p.valuesMasked),
	}
}

// Stored is always true: Absorb writes the batch before it returns, so nothing
// a batch produced is still owed to storage.
func (p *Pipeline) Stored(time.Time, written) bool { return true }

// Absorb decodes one polled batch, masks it, flattens it to rows and writes
// them. A message it cannot decode is parked before its offset can move.
func (p *Pipeline) Absorb(ctx context.Context, polled consume.Polled) (written, error) {
	p.block.reset()
	messages, masked := 0, 0
	for _, record := range polled.Records {
		rows, maskedHere, err := p.absorbMessage(polled.At, record.Value)
		if err != nil {
			if parkErr := p.loop.Park(ctx, record, err); parkErr != nil {
				return written{}, parkErr
			}
			continue
		}
		for _, row := range rows {
			p.block.append(row)
		}
		masked += maskedHere
		messages++
	}

	rows := p.block.rows()
	if err := p.insertBounded(ctx); err != nil {
		return written{}, err
	}
	p.logsWritten += countOf(rows)
	p.valuesMasked += countOf(masked)

	p.log.DebugContext(ctx, "batch absorbed",
		slog.Int("messages", messages),
		slog.Int("logs", rows),
		slog.Int("values_masked", masked),
	)
	return written{}, nil
}

// absorbMessage runs one Kafka message through decode → mask → flatten.
func (p *Pipeline) absorbMessage(now time.Time, message []byte) ([]logRow, int, error) {
	payload, err := decodeEnvelope(message)
	if err != nil {
		return nil, 0, err
	}

	projectID := payload.envelope.GetProjectId()
	// Mask first: from here on no stage can see the original values.
	masked := p.masker.MaskResourceLogs(projectID, payload.request.GetResourceLogs())

	rawDays, _ := p.retention.Retention(projectID)
	marks := stamps{
		receivedAt:    consume.ReceivedAt(payload.envelope, now),
		retentionDays: rawDays,
	}
	return payload.rows(marks), masked, nil
}

// Settle has nothing to do: every batch is written inside Absorb, so nothing
// falls due between turns of the loop.
func (p *Pipeline) Settle(context.Context, time.Time) error { return nil }

// Drain has nothing to write for the same reason. A batch polled but never
// absorbed is simply not committed, and the next owner of the partition reads
// it again.
func (p *Pipeline) Drain(context.Context, time.Time) error { return nil }

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
