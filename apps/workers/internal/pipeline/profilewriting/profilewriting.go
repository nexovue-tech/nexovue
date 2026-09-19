// Package profilewriting runs the profile-ingest pipeline: it consumes the
// ProfileSamples the receiver converted and writes one `profile_samples` row per
// sample (F17).
//
// It never reads OTLP profiles. The receiver converts them (ADR-015), so a change
// to that still-unstable format stops at the receiver and this package does not
// move.
//
// Only the stages are here. The consume loop, offsets, retry budget, dead-letter
// parking and the heartbeat are internal/pipeline/consume, as for every signal.
package profilewriting

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/twmb/franz-go/pkg/kgo"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

// MaxRetentionDays caps how long profiles are kept whatever the project's raw
// retention: they are the largest signal by rows (F17 spec, D2).
const MaxRetentionDays = 14

// jsonAsStringSetting lets a JSON column travel the native protocol as one JSON
// document per row, the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// AttributeMasker rewrites a ProfileSamples' attributes in place, so no stage after
// it can see the original values.
type AttributeMasker interface {
	MaskProfileSamples(projectID string, samples *nexovuev1.ProfileSamples) int
}

// RetentionSource answers how long one project's data is kept.
type RetentionSource interface {
	Retention(projectID string) (raw, derived uint16)
}

// Options are the collaborators the pipeline does not own.
type Options struct {
	// Masking is required: an unmasked value never reaches the store.
	Masking AttributeMasker
	// Retention is optional; without it every row gets the deployment default.
	Retention RetentionSource
}

type staticRetention struct{ raw, derived uint16 }

func (s staticRetention) Retention(string) (uint16, uint16) { return s.raw, s.derived }

// Pipeline is consume → decode → mask → rows → write → commit-when-stored for
// profiles. Like the metric pipeline it holds nothing between batches: rows are
// written before Absorb returns, so Stored is always true.
type Pipeline struct {
	loop          *consume.Loop[stored]
	pool          *chpool.Pool
	storedThrough *ingeststate.WriteGuard
	block         *block
	masker        AttributeMasker
	retention     RetentionSource
	maxRows       int

	written       uint64
	parked        uint64
	masked        uint64
	alreadyStored uint64

	log *slog.Logger
}

type stored struct{}

// New builds the pipeline and joins the consumer group.
func New(cfg config.Config, pool *chpool.Pool, log *slog.Logger, opts Options) (*Pipeline, error) {
	if opts.Masking == nil {
		return nil, errors.New(
			"profile-ingest needs a masker: sample labels carry customer data, " +
				"and an unmasked value must never reach the store")
	}
	guard := ingeststate.NewWriteGuard(ingeststate.NewIngestWatermarks(pool))
	loop, err := consume.Dial[stored](consume.Settings{
		Name:          string(config.PipelineProfileIngest),
		Brokers:       cfg.KafkaBrokers,
		Topic:         cfg.ProfileIngest.Topic,
		ConsumerGroup: cfg.ProfileIngest.ConsumerGroup,
		DLQTopic:      cfg.ProfileIngest.DLQTopic,
		MaxMessages:   cfg.ProfileIngest.MaxMessages,
		MaxBytes:      cfg.ProfileIngest.MaxBytes,
		MaxDelay:      cfg.ProfileIngest.MaxDelay,
		// A partition can arrive from another worker, whose watermark this
		// process never read.
		OnAssigned: guard.Invalidate,
	}, log)
	if err != nil {
		return nil, err
	}

	retention := opts.Retention
	if retention == nil {
		retention = staticRetention{raw: cfg.Retention.RawDays, derived: cfg.Retention.DerivedDays}
	}
	return &Pipeline{
		loop:          loop,
		pool:          pool,
		storedThrough: guard,
		block:         newBlock(),
		masker:        opts.Masking,
		retention:     retention,
		maxRows:       cfg.ProfileIngest.MaxRows,
		log:           log.With(slog.String("pipeline", string(config.PipelineProfileIngest))),
	}, nil
}

// Close leaves the consumer group and releases the broker clients.
func (p *Pipeline) Close() { p.loop.Close() }

// Run consumes until ctx is cancelled.
func (p *Pipeline) Run(ctx context.Context) error { return p.loop.Run(ctx, p) }

// Running reports whether the consume loop is live, for the readiness probe.
func (p *Pipeline) Running(ctx context.Context) error { return p.loop.Running(ctx) }

// Describe is what the startup line says beyond the topic and batch limits.
func (p *Pipeline) Describe() []slog.Attr {
	return []slog.Attr{
		slog.String("profile_table", table),
		slog.Int("insert_max_rows", p.maxRows),
		slog.Int("max_retention_days", MaxRetentionDays),
	}
}

// Holding is what the heartbeat says: running totals; parked climbing names a
// problem.
func (p *Pipeline) Holding() []slog.Attr {
	return []slog.Attr{
		slog.Uint64("profile_samples_written", p.written),
		slog.Uint64("messages_already_stored", p.alreadyStored),
		slog.Uint64("messages_parked", p.parked),
		slog.Uint64("values_masked", p.masked),
	}
}

// Stored is always true: Absorb writes a batch's rows before it returns.
func (p *Pipeline) Stored(time.Time, stored) bool { return true }

// Settle has nothing to write: nothing is held between batches.
func (p *Pipeline) Settle(context.Context, time.Time) error { return nil }

// Drain has nothing to write, for the same reason.
func (p *Pipeline) Drain(context.Context, time.Time) error { return nil }

// Absorb decodes one polled batch, masks it, and writes it in chunks of at most
// maxRows, each followed by the watermark of the offsets it covered.
func (p *Pipeline) Absorb(ctx context.Context, polled consume.Polled) (stored, error) {
	if err := p.loop.Retry(ctx, "read written offsets", p.storedThrough.Refresh); err != nil {
		return stored{}, err
	}

	p.block.reset()
	for at, record := range polled.Records {
		// At or below the watermark: the store acknowledged these rows before the
		// process died, so the replay skips them whole.
		if !p.storing(record) {
			p.alreadyStored++
			continue
		}
		rows, err := p.absorbMessage(polled.At, record.Value)
		if err != nil {
			if parkErr := p.loop.Park(ctx, record, err); parkErr != nil {
				return stored{}, parkErr
			}
			p.parked++
			continue
		}
		for _, r := range rows {
			p.block.append(r)
		}
		if p.block.rows() < p.maxRows {
			continue
		}
		if err := p.writeChunk(ctx, polled.Positions[:at+1]); err != nil {
			return stored{}, err
		}
	}
	return stored{}, p.writeChunk(ctx, polled.Positions)
}

// writeChunk inserts what has accumulated, then records how far the batch's
// offsets are written — in that order, and only after ClickHouse acknowledged:
// a watermark claimed before the insert would make a replay skip rows nothing
// wrote.
func (p *Pipeline) writeChunk(ctx context.Context, covered []consume.Position) error {
	rows := p.block.rows()
	if rows > 0 {
		if err := p.loop.Retry(ctx, "insert "+table, func(callCtx context.Context) error {
			return p.insert(callCtx, rows)
		}); err != nil {
			return err
		}
		p.written += uint64(rows) //nolint:gosec // a row count is never negative
	}
	p.block.reset()
	if len(covered) == 0 {
		return nil
	}
	return p.loop.Retry(ctx, "record written offsets", func(callCtx context.Context) error {
		return p.storedThrough.Advance(callCtx, covered)
	})
}

func (p *Pipeline) insert(ctx context.Context, rows int) error {
	query := ch.Query{
		Body:     p.block.columns.Into(table),
		Input:    p.block.columns,
		Settings: []ch.Setting{{Key: jsonAsStringSetting, Value: "1"}},
	}
	if err := p.pool.Do(ctx, query); err != nil {
		return fmt.Errorf("insert %d rows into %s: %w", rows, table, err)
	}
	return nil
}

func (p *Pipeline) storing(record *kgo.Record) bool {
	return p.storedThrough.Storing(
		ingeststate.Partition{Topic: record.Topic, ID: record.Partition}, record.Offset)
}

// absorbMessage runs one message through decode → mask → rows.
func (p *Pipeline) absorbMessage(now time.Time, message []byte) ([]row, error) {
	payload, err := decodeEnvelope(message)
	if err != nil {
		return nil, err
	}

	// Mask first: from here on no stage can see the original values.
	projectID := payload.envelope.GetProjectId()
	masked := p.masker.MaskProfileSamples(projectID, payload.samples)
	p.masked += uint64(max(masked, 0)) //nolint:gosec // clamped to non-negative

	raw, _ := p.retention.Retention(projectID)
	return payload.flatten(stamps{
		receivedAt:    consume.ReceivedAt(payload.envelope, now),
		retentionDays: profileRetention(raw),
	}), nil
}

// profileRetention is the project's raw retention, capped at MaxRetentionDays.
func profileRetention(raw uint16) uint16 {
	if raw == 0 || raw > MaxRetentionDays {
		return MaxRetentionDays
	}
	return raw
}
