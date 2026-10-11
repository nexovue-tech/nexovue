package usage

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// DefaultFlushInterval is how long counters accumulate in memory before they
// are written. Long enough that a busy tenant costs one round trip rather than
// one per batch, short enough that the usage screen is never far behind and
// that a crash replays only seconds of work.
//
// It bounds this job's commit lag the way the sampling window bounds trace
// assembly's: offsets cannot move until the counters they produced are stored.
const DefaultFlushInterval = 10 * time.Second

// Options are what the pipeline does not own.
type Options struct {
	// Store is required: this job exists to write the control plane, and a
	// meter with nowhere to write counts nothing.
	Store Store

	// Resends skips a sender's resend, as the writer of the same topic does
	// (group 9, C2). Nil skips nothing.
	Resends consume.Resends

	// FlushInterval overrides DefaultFlushInterval. There is no environment
	// variable for it, because this job has no configuration surface of its
	// own yet — see the note on consumer groups in usage.go.
	FlushInterval time.Duration
}

// Pipeline meters one ingestion topic. It is consume → decode → count →
// flush-and-commit: nothing is written per message, and a batch's offsets move
// only once the counters it produced are in Postgres.
type Pipeline struct {
	loop     *consume.Loop[receipt]
	counting *counting

	topic         string
	flushInterval time.Duration
	nextFlush     time.Time
	log           *slog.Logger
}

// receipt is what one batch still owes storage: the flush it is waiting for.
// Its offsets may move once a later flush has committed, because a flush writes
// everything absorbed before it in one transaction.
type receipt struct{ flushes uint64 }

// New builds one signal's meter and joins its consumer group. Close releases
// only what New created; the caller owns the store.
func New(cfg config.Config, signal Signal, log *slog.Logger, opts Options) (*Pipeline, error) {
	if opts.Store == nil {
		return nil, errors.New(signal.Name + ": a store is required")
	}

	settings := signal.topic(cfg)
	scoped := log.With(slog.String("pipeline", signal.Name))
	pipeline := &Pipeline{
		topic:         settings.name,
		flushInterval: flushInterval(opts),
		log:           scoped,
	}
	// Built before the client, because the rebalance callbacks below reach into
	// it: Dial starts consuming, so a rebalance can be signalled before Dial has
	// returned. Its retrier reads the loop lazily, and only Absorb and Settle
	// call it — both on the loop's own goroutine, long after Run has it.
	pipeline.counting = newCounting(signal, opts.Store, pipeline.retry, scoped)

	loop, err := consume.Dial[receipt](consume.Settings{
		Name:          signal.Name,
		Brokers:       cfg.KafkaBrokers,
		Topic:         settings.name,
		ConsumerGroup: settings.consumerGroup,
		// The writers' verdict on resends, so a resend is not billed either.
		Resends:     opts.Resends,
		DLQTopic:    settings.dlqTopic,
		MaxMessages: settings.maxMessages,
		MaxBytes:    settings.maxBytes,
		MaxDelay:    settings.maxDelay,
		// A partition can arrive from another worker, whose flushes this
		// process never saw. Re-reading the durable watermark before counting
		// anything is what keeps the handover from counting a batch twice.
		OnAssigned: func() { pipeline.counting.invalidate() },
		// The other half of the same fact. A partition leaving takes the
		// counters this process has metered but not flushed with it, or they are
		// billed here and again by the worker that took it over.
		OnRevoked: pipeline.release,
		OnLost:    pipeline.releaseLost,
	}, log)
	if err != nil {
		return nil, err
	}

	pipeline.loop = loop
	return pipeline, nil
}

// retry is the consume loop's bounded call out of the process, reached through
// the pipeline so the counting can be built before the loop exists.
func (p *Pipeline) retry(ctx context.Context, what string, op func(context.Context) error) error {
	return p.loop.Retry(ctx, what, op)
}

// release drops what the meter still holds for partitions the group has taken
// back while the session is live.
func (p *Pipeline) release(disowned map[string][]int32) {
	p.counting.disown(p.topic, disowned[p.topic])
}

// releaseLost is release for a session that is already gone.
//
// The release itself is identical — dropping claims nothing, so it is as safe
// against a broker that has stopped believing in us as against one that has
// not. What differs is the durable watermark: these partitions may have belonged
// to another worker for a while, and its flushes are not in this process's cache,
// so it is marked for re-reading here rather than waiting for an assignment that
// may never come.
func (p *Pipeline) releaseLost(disowned map[string][]int32) {
	p.release(disowned)
	p.counting.invalidate()
}

func flushInterval(opts Options) time.Duration {
	if opts.FlushInterval > 0 {
		return opts.FlushInterval
	}
	return DefaultFlushInterval
}

// Close leaves the consumer group and releases the broker clients.
func (p *Pipeline) Close() { p.loop.Close() }

// Run consumes until ctx is cancelled, then flushes whatever it still holds.
func (p *Pipeline) Run(ctx context.Context) error { return p.loop.Run(ctx, p) }

// Running reports whether the consume loop is live, for the readiness probe.
func (p *Pipeline) Running(ctx context.Context) error { return p.loop.Running(ctx) }

// Describe is what the startup line says about this pipeline beyond its topic
// and batch limits.
func (p *Pipeline) Describe() []slog.Attr {
	return []slog.Attr{slog.Duration("flush_interval", p.flushInterval)}
}

// Holding is what the heartbeat says. The one that shows a wedge is
// tenant_days_pending climbing while usage_rows_written stays put.
func (p *Pipeline) Holding() []slog.Attr {
	totals := p.counting.totals
	return []slog.Attr{
		slog.Int("tenant_days_pending", p.counting.meter.Pending()),
		slog.Int64("messages_metered", totals.messages),
		slog.Int64("messages_replayed", totals.replayed),
		slog.Int64("messages_undecodable", totals.undecodable),
		slog.Int64("spans_metered", totals.counters.Spans),
		slog.Int64("logs_metered", totals.counters.Logs),
		slog.Int64("metric_points_metered", totals.counters.MetricPoints),
		slog.Int64("bytes_metered", totals.counters.Bytes),
		// The part of bytes_metered the browser door's budget is charged for. Flat
		// at zero until a page exports.
		slog.Int64("browser_bytes_metered", totals.counters.BrowserBytes),
		slog.Int64("usage_rows_written", totals.rowsWritten),
		slog.Int64("usage_rows_unattributed", totals.rowsUnmatched),
		slog.Int64("services_recorded", totals.servicesFound),
		// What a handover took back rather than billing twice. It steps at each
		// rebalance and is flat otherwise.
		slog.Int64("tenant_days_disowned", totals.disownedTenantDays),
		slog.Int64("bytes_disowned", totals.disownedBytes),
	}
}

// Stored reports whether a batch's counters have reached Postgres. A flush
// writes everything absorbed before it, so any flush after the batch settles it.
func (p *Pipeline) Stored(_ time.Time, produced receipt) bool {
	return p.counting.flushes > produced.flushes
}

// Absorb counts one polled batch. A message it cannot read is reported rather
// than parked — see reportUndecodable.
func (p *Pipeline) Absorb(ctx context.Context, polled consume.Polled) (receipt, error) {
	if err := p.counting.absorb(ctx, polled.Records, polled.At); err != nil {
		return receipt{}, err
	}
	return receipt{flushes: p.counting.flushes}, nil
}

// Settle writes the counters once the flush interval has elapsed. The loop
// commits whatever that made storable.
func (p *Pipeline) Settle(ctx context.Context, now time.Time) error {
	if now.Before(p.nextFlush) {
		return nil
	}
	p.nextFlush = now.Add(p.flushInterval)
	return p.counting.flush(ctx)
}

// Drain writes everything still held, which is what makes a planned shutdown
// lose nothing.
func (p *Pipeline) Drain(ctx context.Context, _ time.Time) error {
	return p.counting.flush(ctx)
}
