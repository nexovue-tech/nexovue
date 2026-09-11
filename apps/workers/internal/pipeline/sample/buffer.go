package sample

import (
	"errors"
	"time"
)

// Facts are what the sampler needs to know about a span. The row itself is
// opaque to this package: the buffer stores and returns it untouched.
type Facts struct {
	// TenantID and ProjectID are part of a trace's identity, not decoration.
	// A trace id is not unique across tenants: W3C trace context is designed to
	// propagate between organisations, so one customer calling another's API
	// forwards its traceparent and both export the same id to us. Keyed on the
	// id alone, one tenant's verdict decided the other's spans — and a cached
	// "drop" silently deleted errored spans that policy keeps in full.
	TenantID  string
	TraceID   string
	ProjectID string
	Start     time.Time
	Duration  time.Duration
	Errored   bool

	// TraceState is the span's raw W3C tracestate, read only for an explicit
	// `ot=rv:` randomness value. Empty is normal and harmless: the decision
	// then comes from the randomness in the trace id itself.
	TraceState string

	// Partition is the Kafka partition the span's record arrived on. It is
	// provenance rather than a sampling input: the buffer keeps it so that a
	// partition handed to another worker can have its spans taken back out. The
	// topic is implicit — one buffer serves one pipeline, and a pipeline consumes
	// one topic.
	Partition int32
}

// Options bound the buffer. Every limit is required: an unbounded map on the
// ingest path is an outage waiting for a traffic spike, and a window with no
// value would hold traces forever.
type Options struct {
	// Window is how long a trace waits for the rest of its spans. It is the
	// floor under ingestion lag, so it is traded against the p99
	// span-to-queryable budget rather than chosen for sampling accuracy alone.
	Window time.Duration

	// DecisionTTL is how long a closed trace's verdict is remembered, so a span
	// arriving after the window follows its trace instead of being judged alone.
	DecisionTTL time.Duration

	// MaxTraces and MaxSpans cap the buffer. Reaching either closes the oldest
	// traces early: their decision is taken on what has arrived so far, which
	// is a worse sample than waiting but is never lost data.
	MaxTraces int
	MaxSpans  int

	// FirstTraces is how many traces a project stores whole before the
	// percentage applies to it at all, so a new customer's first span is never
	// the one the sampler dropped. Zero switches the carve-out off.
	FirstTraces int
}

func (o Options) validate() error {
	switch {
	case o.Window <= 0:
		return errors.New("sample: Window must be positive")
	case o.DecisionTTL < 0:
		return errors.New("sample: DecisionTTL must not be negative")
	case o.MaxTraces <= 0:
		return errors.New("sample: MaxTraces must be positive")
	case o.MaxSpans <= 0:
		return errors.New("sample: MaxSpans must be positive")
	case o.FirstTraces < 0:
		return errors.New("sample: FirstTraces must not be negative")
	}
	return nil
}

// Stats are the buffer's running totals, for logging and for tests.
type Stats struct {
	OpenTraces    int
	BufferedSpans int
	TracesKept    uint64
	TracesDropped uint64
	SpansKept     uint64
	SpansDropped  uint64
	// ClosedEarly counts traces decided before their window ran out because the
	// buffer was full. A number that keeps climbing means the limits are wrong.
	ClosedEarly uint64
	// SpansDisowned and TracesDisowned count what left with a partition the
	// consumer group took back. They step at every handover and are flat
	// otherwise; before Disown existed, every one of those spans was written
	// here and again by the worker that took the partition over.
	//
	// int, unlike the running totals above, because they are sums of slice
	// lengths: agreeing on the width removes a conversion rather than adding a
	// bounds check to justify one.
	SpansDisowned  int
	TracesDisowned int
	// FirstTracesKept counts traces this process kept because their project was
	// still inside its first-traces carve-out. It should be a small number that
	// stops growing; one that climbs steadily means carve-outs are not closing,
	// which is the count failing to reach the control plane.
	FirstTracesKept uint64
}

// Buffer holds spans by trace until the trace can be judged whole.
//
// It needs no lock and no shared store: the receiver keys every span by
// trace_id, so Kafka delivers a whole trace to one partition and one consumer.
// A worker restart loses the buffer, which the query-time join covers, and a
// partition handed to another worker loses it on purpose — see Disown.
//
// Not concurrency-safe. The consume loop calls every method from one goroutine,
// and the rebalance callback that calls Disown holds the lock that loop takes
// around its own turn.
type Buffer[T any] struct {
	options  Options
	policies PolicySource

	open      map[traceKey]*openTrace[T]
	openOrder queue[traceKey]
	spans     int

	decided      map[traceKey]verdict
	decidedOrder queue[traceKey]

	stats Stats
}

// traceKey identifies a trace. Comparable, so it keys a map directly, and it
// carries the tenancy because a trace id alone does not identify a trace.
type traceKey struct {
	tenant  string
	project string
	traceID string
}

func keyOf(facts Facts) traceKey {
	return traceKey{tenant: facts.TenantID, project: facts.ProjectID, traceID: facts.TraceID}
}

type openTrace[T any] struct {
	facts traceFacts
	rows  []buffered[T]
	// project is taken from the first span; a trace belongs to one project.
	project  string
	closesAt time.Time
}

// buffered is one held span and the partition its record arrived on. The
// partition travels with the row rather than with the trace because a trace is
// only *normally* one partition's whole world: the receiver keys spans by trace
// id, but nothing in this package may assume it.
type buffered[T any] struct {
	partition int32
	row       T
}

type traceFacts struct {
	traceID string
	// traceState is the first non-empty tracestate seen on the trace. Every
	// span of a trace carries the same r-value, so the first one will do.
	traceState string
	errored    bool
	earliest   time.Time
	latest     time.Time
}

// duration is the whole trace's span, earliest start to latest end. Not the
// root span's duration: a root that returns before its children finish would
// be judged fast while the trace it belongs to is slow.
func (t traceFacts) duration() time.Duration {
	if t.latest.Before(t.earliest) {
		return 0
	}
	return t.latest.Sub(t.earliest)
}

type verdict struct {
	keep      bool
	expiresAt time.Time
}

// New returns a buffer applying the policies source under the given limits.
func New[T any](options Options, policies PolicySource) (*Buffer[T], error) {
	if err := options.validate(); err != nil {
		return nil, err
	}
	return &Buffer[T]{
		options:  options,
		policies: policies,
		open:     make(map[traceKey]*openTrace[T]),
		decided:  make(map[traceKey]verdict),
	}, nil
}

// Add offers one span to the buffer. It returns the rows that must be written
// now: normally none, because the span is being held — but a span whose trace
// was already decided keeps travelling immediately, and a full buffer releases
// the traces it had to close early.
func (b *Buffer[T]) Add(now time.Time, facts Facts, row T) []T {
	key := keyOf(facts)
	if decision, ok := b.decided[key]; ok && now.Before(decision.expiresAt) {
		if !decision.keep {
			b.stats.SpansDropped++
			return nil
		}
		b.stats.SpansKept++
		return []T{row}
	}

	trace, open := b.open[key]
	if !open {
		trace = &openTrace[T]{
			facts:    traceFacts{traceID: facts.TraceID, earliest: facts.Start},
			project:  facts.ProjectID,
			closesAt: now.Add(b.options.Window),
		}
		b.open[key] = trace
		b.openOrder.push(key)
	}
	trace.observe(facts, row)
	b.spans++

	return b.enforceLimits()
}

func (t *openTrace[T]) observe(facts Facts, row T) {
	t.rows = append(t.rows, buffered[T]{partition: facts.Partition, row: row})
	t.facts.errored = t.facts.errored || facts.Errored
	if t.facts.traceState == "" {
		t.facts.traceState = facts.TraceState
	}
	if facts.Start.Before(t.facts.earliest) {
		t.facts.earliest = facts.Start
	}
	if end := facts.Start.Add(facts.Duration); end.After(t.facts.latest) {
		t.facts.latest = end
	}
}

// Expire decides every trace whose window has closed and returns the rows to
// write. It also forgets verdicts that are past their TTL.
func (b *Buffer[T]) Expire(now time.Time) []T {
	var release []T
	for {
		traceID, ok := b.openOrder.peek()
		if !ok {
			break
		}
		trace, live := b.open[traceID]
		if !live {
			b.openOrder.pop()
			continue
		}
		if now.Before(trace.closesAt) {
			break
		}
		b.openOrder.pop()
		release = b.close(now, traceID, trace, release)
	}
	b.forgetExpiredVerdicts(now)
	return release
}

// Drain decides every open trace regardless of its window and returns the rows
// to write. Shutdown calls it: a buffered span that is never written is data
// lost between the broker and the store.
func (b *Buffer[T]) Drain(now time.Time) []T {
	var release []T
	for traceID, trace := range b.open {
		release = b.close(now, traceID, trace, release)
	}
	b.openOrder.reset()
	return release
}

// enforceLimits closes the oldest traces until the buffer is inside both caps.
func (b *Buffer[T]) enforceLimits() []T {
	var release []T
	for len(b.open) > b.options.MaxTraces || b.spans > b.options.MaxSpans {
		traceID, ok := b.openOrder.pop()
		if !ok {
			break
		}
		trace, live := b.open[traceID]
		if !live {
			continue
		}
		b.stats.ClosedEarly++
		release = b.close(trace.closesAt, traceID, trace, release)
	}
	return release
}

// close takes the verdict for one trace, remembers it and releases its rows.
func (b *Buffer[T]) close(now time.Time, key traceKey, trace *openTrace[T], release []T) []T {
	policy := b.policies.SamplingPolicy(trace.project, b.options.FirstTraces)
	keep := policy.keeps(trace.facts)

	// Only the carve-out's own traces are counted, so the count is what it has
	// stored and it stops moving the moment the carve-out closes.
	if keep && policy.KeepingFirstTraces {
		b.policies.RecordStoredTrace(trace.project)
		b.stats.FirstTracesKept++
	}

	delete(b.open, key)
	b.spans -= len(trace.rows)
	b.rememberVerdict(now, key, keep)

	if !keep {
		b.stats.TracesDropped++
		b.stats.SpansDropped += uint64(len(trace.rows))
		return release
	}
	b.stats.TracesKept++
	b.stats.SpansKept += uint64(len(trace.rows))
	for _, held := range trace.rows {
		release = append(release, held.row)
	}
	return release
}

// Disown drops every buffered span that arrived on one of the given partitions,
// and reports what it dropped.
//
// This is not data loss, and the reason is the ordering the watermark already
// rests on: a batch's offsets are committed only once its sampling window has
// closed, so everything still buffered belongs to records the broker has not
// been told about. The worker that takes the partition over reads those same
// records and writes the rows this one was holding. Keeping them is what writes
// them twice.
//
// Remembered verdicts are left alone. They carry no partition, they are policy
// rather than data, and a trace dropped here was never closed, so it never
// recorded one.
func (b *Buffer[T]) Disown(partitions []int32) Disowned {
	if len(partitions) == 0 {
		return Disowned{}
	}
	leaving := make(map[int32]struct{}, len(partitions))
	for _, partition := range partitions {
		leaving[partition] = struct{}{}
	}

	var dropped Disowned
	for key, trace := range b.open {
		kept := trace.rows[:0]
		for _, held := range trace.rows {
			if _, gone := leaving[held.partition]; gone {
				dropped.Spans++
				continue
			}
			kept = append(kept, held)
		}
		clear(trace.rows[len(kept):])
		trace.rows = kept
		if len(kept) == 0 {
			delete(b.open, key)
			dropped.Traces++
		}
	}

	// openOrder keeps the emptied traces' keys; Expire already skips a key
	// whose trace is gone, so there is nothing to compact here.
	b.spans -= dropped.Spans
	b.stats.SpansDisowned += dropped.Spans
	b.stats.TracesDisowned += dropped.Traces
	return dropped
}

// Disowned is what leaving a partition cost: spans taken back out of the
// buffer, and traces that had nothing left afterwards.
type Disowned struct {
	Spans  int
	Traces int
}

func (b *Buffer[T]) rememberVerdict(now time.Time, key traceKey, keep bool) {
	if b.options.DecisionTTL == 0 {
		return
	}
	b.decided[key] = verdict{keep: keep, expiresAt: now.Add(b.options.DecisionTTL)}
	b.decidedOrder.push(key)

	// The verdict map is capped by the same trace budget as the buffer, so a
	// flood of one-span traces cannot grow it without bound.
	for len(b.decided) > b.options.MaxTraces {
		oldest, ok := b.decidedOrder.pop()
		if !ok {
			break
		}
		delete(b.decided, oldest)
	}
}

func (b *Buffer[T]) forgetExpiredVerdicts(now time.Time) {
	for {
		traceID, ok := b.decidedOrder.peek()
		if !ok {
			return
		}
		decision, held := b.decided[traceID]
		if !held {
			b.decidedOrder.pop()
			continue
		}
		if now.Before(decision.expiresAt) {
			return
		}
		b.decidedOrder.pop()
		delete(b.decided, traceID)
	}
}

// Stats returns the running totals plus what is held right now.
func (b *Buffer[T]) Stats() Stats {
	current := b.stats
	current.OpenTraces = len(b.open)
	current.BufferedSpans = b.spans
	return current
}
