// Package consume is the machinery every worker pipeline shares: it consumes
// one topic, hands each polled batch to one signal's stages, and moves the
// offsets only once everything that batch produced has reached storage.
//
// The pipelines differ in what they decode and where they write. What they must
// not differ in is the discipline around it — at-least-once delivery, a bounded
// call out of the process, a message that cannot be decoded parked with the
// tenant it belonged to before its offset moves, and a loop that has stopped
// turning the container red instead of serving a healthy /readyz while
// consuming nothing. Every one of those was learned from a failure; a second
// copy would have to learn them again.
//
// The shape a pipeline implements is consume → decode → transform → write →
// commit-when-stored. Only the middle is a signal's own work; Processor is
// where it plugs in.
package consume

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
)

// drainTimeout bounds the final write on shutdown. It is short because the work
// is one insert of what is already in memory, and because a worker that will
// not exit gets killed instead of drained.
const drainTimeout = 15 * time.Second

// heartbeatInterval is how often a pipeline says what it is holding. Without it
// a healthy worker and a dead one are identical in the logs: one startup line
// and then nothing. Periodic rather than per-batch, because a batch closes every
// couple of seconds and a line each would be unreadable under load.
//
// Operational logging to stdout, not the self-instrumentation ADR 0053 defers —
// it is the container-log fallback that ADR's second rule keeps independent of
// the OTel pipeline, for reading when that pipeline is the broken thing.
const heartbeatInterval = 30 * time.Second

// Processor is the signal-specific half of a pipeline: what a batch of records
// produces, when what it produced has reached storage, and what it is holding
// meanwhile.
//
// S is whatever a batch has to be remembered by in order to answer Stored — a
// deadline, the buckets it fed, or an empty struct for a pipeline that writes
// its records where it decodes them.
type Processor[S any] interface {
	// Absorb runs one polled batch through the signal's stages. A record it
	// cannot decode must be parked with Loop.Park before Absorb returns, or the
	// batch's offsets would move over a message nothing has seen. An error ends
	// the pipeline, and so the process.
	Absorb(ctx context.Context, polled Polled) (S, error)

	// Settle writes whatever has fallen due since the last turn of the loop.
	// Offsets are committed straight after it, so anything a held batch is
	// still waiting on must be written here.
	Settle(ctx context.Context, now time.Time) error

	// Stored reports whether everything a batch produced has reached storage.
	// Until it does, that batch's offsets stay uncommitted.
	Stored(now time.Time, produced S) bool

	// Drain writes everything still held. It runs once, on shutdown, on a
	// context of its own rather than the cancelled one.
	Drain(ctx context.Context, now time.Time) error

	// Holding is what the signal contributes to the heartbeat. Field names
	// follow the control plane's request log — snake_case, and a duration named
	// `*_ms` written as an integer — so one query reads the data plane and the
	// control plane together.
	Holding() []slog.Attr

	// Describe is what it contributes to the line logged at startup.
	Describe() []slog.Attr
}

// Polled is one batch as it came off the broker.
type Polled struct {
	Records []*kgo.Record

	// Positions are the same records' coordinates, read once here because both
	// the commit queue and the stages need them.
	Positions []Position

	// At is when the batch was polled. It is the pipeline's clock for this
	// batch — passed rather than read again, so the whole batch is judged at
	// one instant.
	At time.Time
}

// Settings are what one pipeline needs to consume a topic. They come from the
// worker's configuration; each signal has its own variables for them.
type Settings struct {
	// Name is the pipeline's name from WORKER_PIPELINES. It is a log field on
	// every line the loop writes and the subject of its startup message.
	Name          string
	Brokers       []string
	Topic         string
	ConsumerGroup string
	DLQTopic      string

	// MaxRows and MaxDelay bound one batch: enough rows for an insert the store
	// merges cheaply, and a deadline that keeps a quiet tenant's rows moving.
	MaxRows  int
	MaxDelay time.Duration

	// OnAssigned runs when the consumer group hands this worker partitions. A
	// partition can arrive from another worker whose durable state this process
	// never saw, and a cache of that state has to be re-read before it is used.
	OnAssigned func()
}

func (s Settings) validate() error {
	var problems []error
	for name, value := range map[string]string{
		"name": s.Name, "topic": s.Topic,
		"consumer group": s.ConsumerGroup, "dead-letter topic": s.DLQTopic,
	} {
		if value == "" {
			problems = append(problems, fmt.Errorf("%s is required", name))
		}
	}
	if len(s.Brokers) == 0 {
		problems = append(problems, errors.New("brokers are required"))
	}
	if s.MaxRows <= 0 {
		problems = append(problems, fmt.Errorf("batch max rows is %d, want more than 0", s.MaxRows))
	}
	if s.MaxDelay <= 0 {
		problems = append(problems, fmt.Errorf("batch max delay is %s, want more than 0", s.MaxDelay))
	}
	return errors.Join(problems...)
}

// Loop consumes a topic and commits its offsets only once the stages have
// stored what they produced from them.
type Loop[S any] struct {
	consumer *kgo.Client
	producer *kgo.Client
	parked   *dlq.Queue
	queue    Queue[S]

	// running is what readiness reports. A worker whose pipeline has stopped is
	// not ready, however healthy its dependencies look — the failure that taught
	// us this presented as a green container consuming nothing for nine minutes.
	running atomic.Bool

	settings Settings
	log      *slog.Logger
	now      func() time.Time // replaced in tests
}

// Dial joins the consumer group and opens the dead-letter producer. Close
// releases both; the caller owns everything else its stages hold.
func Dial[S any](settings Settings, log *slog.Logger) (*Loop[S], error) {
	if err := settings.validate(); err != nil {
		return nil, fmt.Errorf("%s pipeline settings: %w", settings.Name, err)
	}

	consumer, err := kgo.NewClient(
		kgo.SeedBrokers(settings.Brokers...),
		kgo.ConsumeTopics(settings.Topic),
		kgo.ConsumerGroup(settings.ConsumerGroup),
		kgo.OnPartitionsAssigned(func(context.Context, *kgo.Client, map[string][]int32) {
			if settings.OnAssigned != nil {
				settings.OnAssigned()
			}
		}),
		// Marks are what MarkCommitOffsets records; without this option both
		// it and CommitMarkedOffsets are silent no-ops. Autocommit can then
		// only ever commit offsets we have already stored.
		kgo.AutoCommitMarks(),
		// A rebalance between polling and committing would let us commit
		// offsets for partitions we no longer own. Blocking it is bounded by
		// one loop iteration, which AllowRebalance ends.
		kgo.BlockRebalanceOnPoll(),
	)
	if err != nil {
		return nil, fmt.Errorf("%s consumer: %w", settings.Name, err)
	}

	// A separate client for the dead-letter topic: parking a message must not
	// depend on the state of the consumer's fetch session, and the two have
	// nothing else in common.
	producer, err := kgo.NewClient(kgo.SeedBrokers(settings.Brokers...))
	if err != nil {
		consumer.Close()
		return nil, fmt.Errorf("%s dead-letter producer: %w", settings.Name, err)
	}

	return &Loop[S]{
		consumer: consumer,
		producer: producer,
		parked:   dlq.New(producer, settings.DLQTopic, settings.ConsumerGroup),
		settings: settings,
		log:      log.With(slog.String("pipeline", settings.Name)),
		now:      time.Now,
	}, nil
}

// Close leaves the consumer group and releases both clients.
func (l *Loop[S]) Close() {
	l.consumer.Close()
	l.producer.Close()
}

// Running reports whether the consume loop is live. Registered as a readiness
// probe so a stopped pipeline turns the container red instead of leaving it
// healthy and idle.
func (l *Loop[S]) Running(context.Context) error {
	if !l.running.Load() {
		return fmt.Errorf("%s is not running", l.settings.Name)
	}
	return nil
}

// Reached reports the held offsets whose output the stages already count as
// stored, without committing them. See Queue.Reached.
func (l *Loop[S]) Reached(stored func(S) bool) []Position {
	return l.queue.Reached(stored)
}

// Run consumes until ctx is cancelled, then drains whatever it still holds.
//
// Each turn of the loop does the same four things: take in whatever has
// arrived, hand a full batch to the stages, let them settle whatever has fallen
// due, and commit the offsets of every batch whose output is now stored.
func (l *Loop[S]) Run(ctx context.Context, stages Processor[S]) error {
	l.log.LogAttrs(ctx, slog.LevelInfo, l.settings.Name+" started",
		append(l.describe(), stages.Describe()...)...)
	l.running.Store(true)
	defer l.running.Store(false)
	defer l.log.InfoContext(ctx, l.settings.Name+" stopped")

	var pending []*kgo.Record
	var flushBy time.Time
	nextHeartbeat := l.now().Add(heartbeatInterval)

	for {
		if ctx.Err() != nil {
			return l.drain(ctx, stages, pending)
		}

		fetches, err := l.poll(ctx, len(pending), flushBy)
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
			flushBy = l.now().Add(l.settings.MaxDelay)
		}

		if l.batchIsReady(pending, flushBy) {
			if err := l.absorb(ctx, stages, pending); err != nil {
				return err
			}
			pending, flushBy = pending[:0], time.Time{}
		}

		now := l.now()
		if err := stages.Settle(ctx, now); err != nil {
			return err
		}
		if err := l.commit(ctx, l.queue.Ready(storedBy(stages, now))); err != nil {
			return err
		}
		if !now.Before(nextHeartbeat) {
			l.heartbeat(ctx, now, stages)
			nextHeartbeat = now.Add(heartbeatInterval)
		}
		// Held since this iteration's poll, so nothing committed above could
		// have raced a reassignment.
		l.consumer.AllowRebalance()
	}
}

// storedBy asks the stages, at one instant, whether a batch's output has
// reached storage.
func storedBy[S any](stages Processor[S], now time.Time) func(S) bool {
	return func(produced S) bool { return stages.Stored(now, produced) }
}

func (l *Loop[S]) describe() []slog.Attr {
	return []slog.Attr{
		slog.String("topic", l.settings.Topic),
		slog.String("group", l.settings.ConsumerGroup),
		slog.String("dlq_topic", l.settings.DLQTopic),
		slog.Int("batch_max_rows", l.settings.MaxRows),
		slog.Duration("batch_max_delay", l.settings.MaxDelay),
	}
}

// heartbeat records what the pipeline is holding, at INFO, on a timer.
func (l *Loop[S]) heartbeat(ctx context.Context, now time.Time, stages Processor[S]) {
	held := []slog.Attr{
		slog.String("topic", l.settings.Topic),
		slog.Int("uncommitted_records", l.queue.Records()),
		// The one that shows a wedge: commits that stop advancing.
		slog.Int64("commit_lag_ms", l.queue.Lag(now).Milliseconds()),
	}
	l.log.LogAttrs(ctx, slog.LevelInfo, "ingest heartbeat",
		append(held, stages.Holding()...)...)
}

func (l *Loop[S]) batchIsReady(pending []*kgo.Record, flushBy time.Time) bool {
	return len(pending) > 0 &&
		(len(pending) >= l.settings.MaxRows || !l.now().Before(flushBy))
}

// poll waits for records, giving up at the flush deadline once a batch has
// started and after one batch delay otherwise. The second deadline is what
// keeps the loop turning while a tenant is quiet: what a pipeline settles
// closes on wall-clock time, not on arrivals.
func (l *Loop[S]) poll(
	ctx context.Context,
	pending int,
	flushBy time.Time,
) (kgo.Fetches, error) {
	deadline := l.now().Add(l.settings.MaxDelay)
	if pending > 0 {
		deadline = flushBy
	}
	pollCtx, cancel := context.WithDeadline(ctx, deadline)
	defer cancel()

	fetches := l.consumer.PollRecords(pollCtx, l.settings.MaxRows-pending)
	if fetches.IsClientClosed() {
		return fetches, errClientClosed
	}
	return fetches, l.reportFetchErrors(ctx, fetches)
}

// errClientClosed ends Run when the consumer is closed under it. That is only
// benign during shutdown; see Run for why the distinction matters.
var errClientClosed = errors.New("kafka consumer closed")

// reportFetchErrors logs what franz-go will retry on its own and returns only
// what Run cannot continue past. A deadline of our own making is the poll timer
// firing, not a fault.
func (l *Loop[S]) reportFetchErrors(ctx context.Context, fetches kgo.Fetches) error {
	for _, fetchErr := range fetches.Errors() {
		switch {
		case errors.Is(fetchErr.Err, context.DeadlineExceeded),
			errors.Is(fetchErr.Err, context.Canceled):
			continue
		default:
			l.log.WarnContext(ctx, "fetch failed, retrying",
				slog.String("topic", fetchErr.Topic),
				slog.Int("partition", int(fetchErr.Partition)),
				slog.String("error", fetchErr.Err.Error()),
			)
		}
	}
	return nil
}

// absorb hands one polled batch to the stages and holds its offsets until what
// the batch produced has been stored.
func (l *Loop[S]) absorb(ctx context.Context, stages Processor[S], records []*kgo.Record) error {
	polled := Polled{Records: records, Positions: PositionsOf(records), At: l.now()}

	produced, err := stages.Absorb(ctx, polled)
	if err != nil {
		return err
	}
	l.queue.Hold(Batch[S]{
		Positions: polled.Positions,
		PolledAt:  polled.At,
		Produced:  produced,
	})
	return nil
}

// drain writes everything still held and commits it, which is what makes a
// planned shutdown lose nothing. It runs on a context of its own: the one that
// ended the loop is already cancelled, and cancelled contexts cannot insert.
func (l *Loop[S]) drain(ctx context.Context, stages Processor[S], unprocessed []*kgo.Record) error {
	drainCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), drainTimeout)
	defer cancel()

	// Records polled but never absorbed are simply not committed: they are
	// still on the broker and the next owner of the partition reads them.
	if len(unprocessed) > 0 {
		l.log.InfoContext(drainCtx, "leaving unprocessed records for redelivery",
			slog.Int("records", len(unprocessed)))
	}

	if err := stages.Drain(drainCtx, l.now()); err != nil {
		return err
	}
	l.log.LogAttrs(drainCtx, slog.LevelInfo, l.settings.Name+" drained",
		append([]slog.Attr{slog.Int("records_committed", l.queue.Records())},
			stages.Holding()...)...)
	return l.commit(drainCtx, l.queue.Drain())
}

func (l *Loop[S]) commit(ctx context.Context, positions []Position) error {
	marks := Markable(positions)
	if marks == nil {
		return nil
	}
	l.consumer.MarkCommitOffsets(marks)
	if err := l.Retry(ctx, "commit offsets", l.consumer.CommitMarkedOffsets); err != nil {
		return fmt.Errorf("commit offsets: %w", err)
	}
	return nil
}
