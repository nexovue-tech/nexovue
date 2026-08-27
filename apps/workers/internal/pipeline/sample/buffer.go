package sample

import (
	"errors"
	"time"
)

// Facts are what the sampler needs to know about a span. The row itself is
// opaque to this package: the buffer stores and returns it untouched.
type Facts struct {
	TraceID   string
	ProjectID string
	Start     time.Time
	Duration  time.Duration
	Errored   bool
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
}

// Buffer holds spans by trace until the trace can be judged whole.
//
// It needs no lock and no shared store: the receiver keys every span by
// trace_id, so Kafka delivers a whole trace to one partition and one consumer
// (ADR 0009). A worker restart loses the buffer, which the query-time join
// covers.
type Buffer[T any] struct {
	options  Options
	policies PolicySource

	open      map[string]*openTrace[T]
	openOrder queue[string]
	spans     int

	decided      map[string]verdict
	decidedOrder queue[string]

	stats Stats
}

type openTrace[T any] struct {
	facts traceFacts
	rows  []T
	// project is taken from the first span; a trace belongs to one project.
	project  string
	closesAt time.Time
}

type traceFacts struct {
	traceID  string
	errored  bool
	earliest time.Time
	latest   time.Time
}

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
		open:     make(map[string]*openTrace[T]),
		decided:  make(map[string]verdict),
	}, nil
}

// Add offers one span to the buffer. It returns the rows that must be written
// now: normally none, because the span is being held — but a span whose trace
// was already decided keeps travelling immediately, and a full buffer releases
// the traces it had to close early.
func (b *Buffer[T]) Add(now time.Time, facts Facts, row T) []T {
	if decision, ok := b.decided[facts.TraceID]; ok && now.Before(decision.expiresAt) {
		if !decision.keep {
			b.stats.SpansDropped++
			return nil
		}
		b.stats.SpansKept++
		return []T{row}
	}

	trace, open := b.open[facts.TraceID]
	if !open {
		trace = &openTrace[T]{
			facts:    traceFacts{traceID: facts.TraceID, earliest: facts.Start},
			project:  facts.ProjectID,
			closesAt: now.Add(b.options.Window),
		}
		b.open[facts.TraceID] = trace
		b.openOrder.push(facts.TraceID)
	}
	trace.observe(facts, row)
	b.spans++

	return b.enforceLimits()
}

func (t *openTrace[T]) observe(facts Facts, row T) {
	t.rows = append(t.rows, row)
	t.facts.errored = t.facts.errored || facts.Errored
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
func (b *Buffer[T]) close(now time.Time, traceID string, trace *openTrace[T], release []T) []T {
	keep := b.policies.SamplingPolicy(trace.project).keeps(trace.facts)

	delete(b.open, traceID)
	b.spans -= len(trace.rows)
	b.rememberVerdict(now, traceID, keep)

	if !keep {
		b.stats.TracesDropped++
		b.stats.SpansDropped += uint64(len(trace.rows))
		return release
	}
	b.stats.TracesKept++
	b.stats.SpansKept += uint64(len(trace.rows))
	return append(release, trace.rows...)
}

func (b *Buffer[T]) rememberVerdict(now time.Time, traceID string, keep bool) {
	if b.options.DecisionTTL == 0 {
		return
	}
	b.decided[traceID] = verdict{keep: keep, expiresAt: now.Add(b.options.DecisionTTL)}
	b.decidedOrder.push(traceID)

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
