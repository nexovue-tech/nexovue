package serviceedges

import (
	"errors"
	"sort"
	"time"
)

// Options bound the accumulator. Every limit is required: an unbounded map on
// the ingest path is an outage waiting for a traffic spike, and a wait with no
// value would hold spans forever.
type Options struct {
	// PairWindow is how long a span waits for its partner. It is processing
	// time, not span time: it answers "how long until the other half is
	// consumed", so a replay of week-old spans pairs exactly as live traffic
	// does.
	//
	// The trace pipeline already holds a whole trace for the sampling window, so
	// that window is the natural value — the spans are in memory anyway, and
	// waiting longer than the pipeline's own buffer buys nothing a longer
	// sampling window would not buy first.
	PairWindow time.Duration

	// LateGrace holds a minute open after it ends, so spans that arrive a little
	// late still land in their own minute.
	LateGrace time.Duration

	// MaxPending caps the pairing buffer. Reaching it settles the span that has
	// waited longest, which is a worse graph but never a lost call.
	MaxPending int
}

func (o Options) validate() error {
	switch {
	case o.PairWindow <= 0:
		return errors.New("serviceedges: PairWindow must be positive")
	case o.LateGrace < 0:
		return errors.New("serviceedges: LateGrace must not be negative")
	case o.MaxPending <= 0:
		return errors.New("serviceedges: MaxPending must be positive")
	}
	return nil
}

// Stats are the accumulator's running totals, for logging and for tests.
type Stats struct {
	PendingSpans int
	OpenEdges    int
	// Paired counts completed edges: a caller and the callee that answered it.
	Paired uint64
	// Expired counts spans settled because their partner did not arrive in time,
	// Evicted those settled because the buffer was full, and Displaced those
	// that found their slot held by a span they could not pair with.
	Expired   uint64
	Evicted   uint64
	Displaced uint64
	// Ignored counts spans that can take no part in a graph: an unusable span
	// id, a root span, or a kind that is neither end of a call.
	Ignored uint64
	// Uninstrumented counts edges drawn to UninstrumentedService.
	Uninstrumented uint64
}

// edgeKey identifies one row of `service_edges_1m`. It is a comparable struct so
// it can key a map without formatting a string per span.
type edgeKey struct {
	minute        time.Time
	tenantID      string
	projectID     string
	clientService string
	serverService string
	edgeKind      string
	// Constant for a given project, but carried here because it sits in the
	// target table's partition key.
	retentionDays uint16
}

type edgeTotals struct {
	calls   uint64
	errors  uint64
	latency latencySamples
}

// Accumulator pairs spans into edges and accumulates them per minute, handing
// out rows once a minute can no longer change.
type Accumulator struct {
	opts    Options
	pending *pairBuffer
	buckets map[edgeKey]*edgeTotals
	stats   Stats
}

// New returns an accumulator, or an error if the options do not bound it.
func New(opts Options) (*Accumulator, error) {
	if err := opts.validate(); err != nil {
		return nil, err
	}
	return &Accumulator{
		opts:    opts,
		pending: newPairBuffer(opts.PairWindow, opts.MaxPending),
		buckets: make(map[edgeKey]*edgeTotals),
	}, nil
}

// Observe folds one span into the graph and returns the minute bucket the
// caller's offset must wait on before it may be committed.
//
// The returned minute is the later of the bucket this span's edge lands in and
// the bucket we are processing in. The first makes the offset wait for the row
// to be written; the second makes it wait for the pairing, so a span still
// sitting in the buffer — including one replayed from a week ago, whose own
// bucket is long settled — cannot have its offset committed before its edge
// exists. Both are bounded, so the bucket always settles.
//
// Every edge is bucketed on its *caller's* span, and a caller's own Observe
// returns exactly that bucket. Kafka offsets commit as a prefix, so the caller's
// record gates every record after it and no edge can be committed before it is
// stored.
//
// discardedSkew is how far this span's own timestamp sat from receive time when
// that timestamp was not trusted, and zero when it was. It is returned rather
// than logged so the caller can report it once per batch and name the tenant
// whose clock is wrong.
func (a *Accumulator) Observe(now time.Time, span Span) (minute time.Time, discardedSkew time.Duration) {
	at, discarded := bucketInstant(span, now)
	for _, resolved := range a.pending.add(observedSpan{span: span, bucketAt: at, arrived: now}) {
		a.record(resolved)
	}
	if !participates(span) {
		return time.Time{}, 0
	}
	return later(startOfMinute(at), startOfMinute(now)), discarded
}

// participates reports whether a span can contribute to the graph at all, which
// is what decides whether it gets a settlement minute.
func participates(span Span) bool {
	if len(span.SpanID) == 0 {
		return false
	}
	if _, isCaller := callerKind(span.Kind); isCaller {
		return true
	}
	_, isCallee := calleeKind(span.Kind)
	return isCallee && len(span.ParentSpanID) > 0
}

// record folds one settled caller — with the callee that answered it, or
// without — into its minute bucket.
func (a *Accumulator) record(resolved resolution) {
	caller := resolved.caller.span
	serverService := UninstrumentedService
	errored := caller.Errored
	if resolved.paired {
		serverService = serviceOf(resolved.callee)
		// Either end is enough to call the request failed: an SDK that maps a
		// 500 to an unset client status still made a call that did not work.
		errored = errored || resolved.callee.Errored
	} else {
		a.stats.Uninstrumented++
	}

	key := edgeKey{
		minute:        startOfMinute(resolved.caller.bucketAt),
		tenantID:      caller.TenantID,
		projectID:     caller.ProjectID,
		clientService: serviceOf(caller),
		serverService: serverService,
		edgeKind:      resolved.edgeKind,
		retentionDays: caller.RetentionDays,
	}

	totals, ok := a.buckets[key]
	if !ok {
		totals = &edgeTotals{}
		a.buckets[key] = totals
	}
	totals.calls++
	if errored {
		totals.errors++
	}
	// Latency is the caller's own duration: what the caller experienced,
	// network included. The callee's view of itself is in `endpoint_stats_1m`.
	totals.latency.observe(weigh(caller.TraceID, caller.SpanID), caller.DurationMS)
}

// Drained reports whether every edge for a minute has already been written. A
// zero minute means nothing was observed, which is trivially drained.
func (a *Accumulator) Drained(now, minute time.Time) bool {
	if minute.IsZero() {
		return true
	}
	return a.closed(now, minute)
}

// closed reports whether a minute can no longer change: it has ended, its grace
// has run out, and any span still waiting to pair when it ended has since been
// settled.
func (a *Accumulator) closed(now, minute time.Time) bool {
	settlesAt := minute.Add(bucketWidth + a.opts.PairWindow + a.opts.LateGrace)
	return !settlesAt.After(now.UTC())
}

// Drain returns rows for every minute that can no longer change, removing them
// from memory. Minutes still open are left alone.
//
// It settles the pairing buffer first: a span whose wait has run out has to
// reach its bucket before that bucket is written, or the edge would be lost to
// a minute that had already gone.
func (a *Accumulator) Drain(now time.Time) []Edge {
	for _, resolved := range a.pending.expire(now) {
		a.record(resolved)
	}
	return a.drain(func(minute time.Time) bool { return a.closed(now, minute) })
}

// DrainAll returns rows for every minute held, complete or not, and settles
// every span still waiting. Shutdown calls it: an unwritten edge is a gap in a
// dependency graph that nothing will ever fill.
func (a *Accumulator) DrainAll() []Edge {
	for _, resolved := range a.pending.drainAll() {
		a.record(resolved)
	}
	return a.drain(func(time.Time) bool { return true })
}

func (a *Accumulator) drain(closed func(minute time.Time) bool) []Edge {
	edges := make([]Edge, 0, len(a.buckets))
	for key, totals := range a.buckets {
		if !closed(key.minute) {
			continue
		}
		edges = append(edges, Edge{
			Minute:        key.minute,
			TenantID:      key.tenantID,
			ProjectID:     key.projectID,
			RetentionDays: key.retentionDays,
			ClientService: key.clientService,
			ServerService: key.serverService,
			Kind:          key.edgeKind,
			CallCount:     totals.calls,
			ErrorCount:    totals.errors,
			LatencyMS:     totals.latency.values(),
		})
		delete(a.buckets, key)
	}

	// Sorted because the block ClickHouse receives must be byte-identical when a
	// restart re-derives the same minute from the same offsets — that is what
	// lets the store's own insert deduplication recognise the retry and drop it.
	// Go randomises map iteration, so without this the same edges produce a
	// different block every time.
	sort.Slice(edges, func(i, j int) bool { return edges[i].less(edges[j]) })
	return edges
}

// less orders edges totally, so a drain is reproducible.
func (e Edge) less(other Edge) bool {
	switch {
	case !e.Minute.Equal(other.Minute):
		return e.Minute.Before(other.Minute)
	case e.TenantID != other.TenantID:
		return e.TenantID < other.TenantID
	case e.ProjectID != other.ProjectID:
		return e.ProjectID < other.ProjectID
	case e.ClientService != other.ClientService:
		return e.ClientService < other.ClientService
	case e.ServerService != other.ServerService:
		return e.ServerService < other.ServerService
	default:
		return e.Kind < other.Kind
	}
}

// Stats reports what is held and what has happened, which is what the pipeline
// logs and what shows a leak if one ever appears.
func (a *Accumulator) Stats() Stats {
	stats := a.stats
	stats.PendingSpans = a.pending.pending()
	stats.OpenEdges = len(a.buckets)
	stats.Paired = a.pending.stats.paired
	stats.Expired = a.pending.stats.expired
	stats.Evicted = a.pending.stats.evicted
	stats.Displaced = a.pending.stats.displaced
	stats.Ignored = a.pending.stats.ignored
	return stats
}
