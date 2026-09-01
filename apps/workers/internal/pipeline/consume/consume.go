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
	"math"
	"sync"
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

// defaultMaxBytes bounds one batch's messages when a pipeline names no bound of
// its own. Six consumer clients run in one worker against a 1 GiB limit, so the
// budget is what six of these plus the Go heap's own headroom fit inside.
const defaultMaxBytes = 16 << 20

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

	// MaxMessages and MaxDelay bound one batch: how many broker messages it
	// holds, and a deadline that keeps a quiet tenant's rows moving.
	//
	// Messages, not the rows they carry. A pipeline that must bound its rows
	// bounds them where it knows how many a message decoded into.
	MaxMessages int
	MaxDelay    time.Duration

	// MaxBytes bounds a batch by the size of the messages in it. MaxMessages
	// cannot: one OTLP message carries as many log records or data points as
	// the sender's exporter batched, so a batch bounded only at 5000 messages
	// held half a gigabyte here and the worker was OOM-killed at its 1 GiB
	// limit. Zero takes defaultMaxBytes, so a pipeline that says nothing is
	// still bounded.
	MaxBytes int

	// OnAssigned runs when the consumer group hands this worker partitions. A
	// partition can arrive from another worker whose durable state this process
	// never saw, and a cache of that state has to be re-read before it is used.
	OnAssigned func()

	// OnRevoked runs when the group takes partitions away while the session is
	// still live, before the handover completes. Whatever a stage is still
	// holding for them has to be released here: their records are uncommitted,
	// so the worker taking the partition over reads them again, and anything
	// this one writes afterwards is a second copy (ADR 0090).
	//
	// It runs on the client's goroutine while the whole group's rebalance
	// waits, so it must be memory work. The loop makes no call out of the
	// process on its behalf.
	OnRevoked func(disowned map[string][]int32)

	// OnLost runs when the session is already gone — a fatal group error, where
	// the partitions may have been someone else's for a while. The same release,
	// and nothing may be written on the way out: a commit or a watermark would
	// be answered by a broker that no longer believes we own anything.
	OnLost func(disowned map[string][]int32)
}

// maxBytes is the batch byte bound in force, which is the built-in default when
// the pipeline named none.
//
// int32 because that is what a Kafka fetch request can express. validate
// refuses anything larger, so the conversion below cannot truncate.
func (s Settings) maxBytes() int32 {
	if s.MaxBytes <= 0 || s.MaxBytes > math.MaxInt32 {
		return defaultMaxBytes
	}
	return int32(s.MaxBytes)
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
	if s.MaxMessages <= 0 {
		problems = append(problems, fmt.Errorf(
			"batch max messages is %d, want more than 0", s.MaxMessages))
	}
	if s.MaxDelay <= 0 {
		problems = append(problems, fmt.Errorf("batch max delay is %s, want more than 0", s.MaxDelay))
	}
	if s.MaxBytes > math.MaxInt32 {
		problems = append(problems, fmt.Errorf(
			"batch max bytes is %d, want at most %d — a Kafka fetch cannot ask for more",
			s.MaxBytes, math.MaxInt32))
	}
	return errors.Join(problems...)
}

// Loop consumes a topic and commits its offsets only once the stages have
// stored what they produced from them.
type Loop[S any] struct {
	consumer *kgo.Client
	producer *kgo.Client
	parked   *dlq.Queue

	// turning serialises one turn of the loop against the rebalance callbacks.
	// It guards batch, queue and everything the stages hold.
	//
	// BlockRebalanceOnPoll almost does this on its own: a poll that returns
	// records blocks the callbacks until AllowRebalance. A poll that returns
	// none does not — franz-go releases the block for an empty fetch — and the
	// loop still absorbs, settles and commits afterwards. Without this, a
	// revoke could run while Settle was releasing traces, which is the one
	// moment it exists to stop.
	turning sync.Mutex
	batch   pendingBatch
	queue   Queue[S]

	// running is what readiness reports. A worker whose pipeline has stopped is
	// not ready, however healthy its dependencies look — the failure that taught
	// us this presented as a green container consuming nothing for nine minutes.
	running atomic.Bool

	nextHeartbeat time.Time

	settings Settings
	log      *slog.Logger
	now      func() time.Time // replaced in tests
}

// pendingBatch is the batch being filled from successive polls: the records,
// what they cost to hold, and when they must be handed on even if the batch is
// not full.
type pendingBatch struct {
	records []*kgo.Record
	bytes   int
	flushBy time.Time
}

func (p *pendingBatch) add(record *kgo.Record) {
	p.records = append(p.records, record)
	p.bytes += sizeOf(record)
}

// openAt starts the batch's flush deadline when its first record lands. That
// deadline is what keeps a quiet tenant's rows moving.
func (p *pendingBatch) openAt(now time.Time, maxDelay time.Duration) {
	if len(p.records) > 0 && p.flushBy.IsZero() {
		p.flushBy = now.Add(maxDelay)
	}
}

func (p *pendingBatch) isReady(now time.Time, settings Settings) bool {
	return len(p.records) > 0 &&
		(len(p.records) >= settings.MaxMessages ||
			p.bytes >= int(settings.maxBytes()) ||
			!now.Before(p.flushBy))
}

func (p *pendingBatch) reset() {
	clear(p.records)
	p.records, p.bytes, p.flushBy = p.records[:0], 0, time.Time{}
}

// forget drops the records of partitions the group has taken away, and reports
// how many. They were polled but never absorbed, so nothing has been produced
// from them and nothing has to be undone — absorbing them now would write rows
// the new owner writes as well.
func (p *pendingBatch) forget(disowned map[string][]int32) int {
	kept := p.records[:0]
	bytes := 0
	for _, record := range p.records {
		if names(disowned, record.Topic, record.Partition) {
			continue
		}
		kept = append(kept, record)
		bytes += sizeOf(record)
	}
	dropped := len(p.records) - len(kept)
	clear(p.records[len(kept):])
	p.records, p.bytes = kept, bytes
	if len(kept) == 0 {
		p.flushBy = time.Time{}
	}
	return dropped
}

// pollPlan is what the next poll may ask for: how many more records this
// batch can take, and when the poll must return regardless.
type pollPlan struct {
	records  int
	deadline time.Time
}

// Dial joins the consumer group and opens the dead-letter producer. Close
// releases both; the caller owns everything else its stages hold.
func Dial[S any](settings Settings, log *slog.Logger) (*Loop[S], error) {
	if err := settings.validate(); err != nil {
		return nil, fmt.Errorf("%s pipeline settings: %w", settings.Name, err)
	}

	// Built before the client, because the rebalance callbacks below reach back
	// into it: a partition leaving has to reach the offsets this loop is
	// holding, not only the stages.
	loop := &Loop[S]{
		settings: settings,
		log:      log.With(slog.String("pipeline", settings.Name)),
		now:      time.Now,
	}

	consumer, err := kgo.NewClient(
		kgo.SeedBrokers(settings.Brokers...),
		kgo.ConsumeTopics(settings.Topic),
		kgo.ConsumerGroup(settings.ConsumerGroup),
		// Bounds what one fetch may return. Without it the broker may hand back
		// 50 MiB (franz-go's default) in a single poll, and the batch bound
		// below would be applied to memory we are already holding.
		kgo.FetchMaxBytes(settings.maxBytes()),
		kgo.OnPartitionsAssigned(func(context.Context, *kgo.Client, map[string][]int32) {
			if settings.OnAssigned != nil {
				settings.OnAssigned()
			}
		}),
		// The two halves of a handover. Without them the losing worker is never
		// told, and it goes on releasing a buffer whose records the gaining
		// worker is already reading — every span in it written twice.
		kgo.OnPartitionsRevoked(func(ctx context.Context, _ *kgo.Client, disowned map[string][]int32) {
			loop.disown(ctx, disowned, revoked)
		}),
		kgo.OnPartitionsLost(func(ctx context.Context, _ *kgo.Client, disowned map[string][]int32) {
			loop.disown(ctx, disowned, lost)
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

	loop.consumer = consumer
	loop.producer = producer
	loop.parked = dlq.New(producer, settings.DLQTopic, settings.ConsumerGroup)
	return loop, nil
}

// revocation is how a partition left. A revoke is cooperative and the session
// is still live; a loss means the session is already gone, so the partitions
// may have belonged to someone else for a while and nothing may be written for
// them on the way out.
type revocation string

const (
	revoked revocation = "revoked"
	lost    revocation = "lost"
)

// disown releases everything this worker still holds for partitions the group
// has taken away: the records polled but not absorbed, the offsets held
// uncommitted, and whatever the stages were keeping.
//
// It runs on the client's goroutine with the whole group's rebalance waiting on
// it, so the work is memory only — no insert, no commit, no watermark. A call
// out of the process here would be bounded by the loop's own two-minute retry
// budget against a sixty-second rebalance timeout, and losing the session would
// move every other partition this worker owns as well.
//
// Nothing is claimed as finished, which is what fixes the direction of the
// failure: the records are still on the broker, so the worst case is that the
// new owner writes something this one already wrote, and never that a span
// nothing wrote is skipped.
func (l *Loop[S]) disown(ctx context.Context, disowned map[string][]int32, how revocation) {
	if len(disowned) == 0 {
		return
	}

	l.turning.Lock()
	defer l.turning.Unlock()

	unpolled := l.batch.forget(disowned)
	uncommitted := l.queue.Forget(disowned)

	release, level := l.settings.OnRevoked, slog.LevelInfo
	if how == lost {
		release, level = l.settings.OnLost, slog.LevelWarn
	}
	if release != nil {
		release(disowned)
	}

	l.log.LogAttrs(ctx, level, "released partitions the group took back",
		slog.String("how", string(how)),
		slog.Any("partitions", disowned),
		slog.Int("unpolled_records", unpolled),
		slog.Int("uncommitted_records", uncommitted),
	)
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

	l.nextHeartbeat = l.now().Add(heartbeatInterval)
	plan := pollPlan{records: l.settings.MaxMessages, deadline: l.now().Add(l.settings.MaxDelay)}

	for {
		if ctx.Err() != nil {
			return l.drain(ctx, stages)
		}

		fetches, err := l.poll(ctx, plan)
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
		plan, err = l.turn(ctx, stages, fetches)
		if err != nil {
			return err
		}
		// Held since this iteration's poll, so nothing committed above could
		// have raced a reassignment.
		l.consumer.AllowRebalance()
	}
}

// turn is one iteration's work: take in what arrived, hand a full batch to the
// stages, let them settle what has fallen due, and commit the offsets of every
// batch whose output is now stored.
//
// All of it runs under the lock the rebalance callbacks take, so a partition
// cannot be taken away in the middle of it. It returns what the next poll may
// ask for, read here rather than in Run because the batch it is read from is
// the callbacks' to change.
func (l *Loop[S]) turn(
	ctx context.Context,
	stages Processor[S],
	fetches kgo.Fetches,
) (pollPlan, error) {
	l.turning.Lock()
	defer l.turning.Unlock()

	fetches.EachRecord(l.batch.add)
	l.batch.openAt(l.now(), l.settings.MaxDelay)

	if l.batch.isReady(l.now(), l.settings) {
		if err := l.absorb(ctx, stages, l.batch.records); err != nil {
			return pollPlan{}, err
		}
		l.batch.reset()
	}

	now := l.now()
	if err := stages.Settle(ctx, now); err != nil {
		return pollPlan{}, err
	}
	if err := l.commit(ctx, l.queue.Ready(storedBy(stages, now))); err != nil {
		return pollPlan{}, err
	}
	if !now.Before(l.nextHeartbeat) {
		l.heartbeat(ctx, now, stages)
		l.nextHeartbeat = now.Add(heartbeatInterval)
	}
	return l.nextPoll(now), nil
}

// nextPoll is how many records the batch can still take and when the poll must
// give up: at the batch's own flush deadline once it has started, and after one
// batch delay otherwise. The second deadline is what keeps the loop turning
// while a tenant is quiet: what a pipeline settles closes on wall-clock time,
// not on arrivals.
func (l *Loop[S]) nextPoll(now time.Time) pollPlan {
	plan := pollPlan{
		records:  l.settings.MaxMessages - len(l.batch.records),
		deadline: now.Add(l.settings.MaxDelay),
	}
	if len(l.batch.records) > 0 {
		plan.deadline = l.batch.flushBy
	}
	return plan
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
		slog.Int("batch_max_messages", l.settings.MaxMessages),
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

// sizeOf is what one record costs to hold. The value dominates; the key and
// headers are counted because a batch of small values with large headers costs
// the same memory and would otherwise be invisible to the bound.
func sizeOf(record *kgo.Record) int {
	size := len(record.Key) + len(record.Value)
	for _, header := range record.Headers {
		size += len(header.Key) + len(header.Value)
	}
	return size
}

// poll waits for records on the plan the last turn of the loop left. It runs
// outside the turn lock: a poll blocks until any rebalance callback in flight
// has finished, and holding the lock across it would deadlock against the
// callback waiting for the lock.
func (l *Loop[S]) poll(ctx context.Context, plan pollPlan) (kgo.Fetches, error) {
	pollCtx, cancel := context.WithDeadline(ctx, plan.deadline)
	defer cancel()

	fetches := l.consumer.PollRecords(pollCtx, plan.records)
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
func (l *Loop[S]) drain(ctx context.Context, stages Processor[S]) error {
	drainCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), drainTimeout)
	defer cancel()

	l.turning.Lock()
	defer l.turning.Unlock()

	// Records polled but never absorbed are simply not committed: they are
	// still on the broker and the next owner of the partition reads them.
	if unprocessed := len(l.batch.records); unprocessed > 0 {
		l.log.InfoContext(drainCtx, "leaving unprocessed records for redelivery",
			slog.Int("records", unprocessed))
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
