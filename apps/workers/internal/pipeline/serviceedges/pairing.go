package serviceedges

import "time"

// pairKey identifies one caller→callee hop while its two spans are still
// arriving.
//
// Both sides park under the *caller's* span id — the caller under its own, the
// callee under its parent's — so one lookup finds a partner whichever half
// arrives first.
//
// Tenancy is part of the key, not decoration. Span ids are only unique within a
// trace, and trace ids are not unique across tenants: W3C trace context is
// designed to propagate between organisations, so two customers can export the
// same ids. Keyed without tenancy, one tenant's spans would pair with another's
// and invent an edge between services that never spoke — the same defect class
// the sampling buffer was fixed for.
type pairKey struct {
	tenantID     string
	projectID    string
	traceID      string
	callerSpanID string
}

// observedSpan is a span as it entered the accumulator: the span itself, the
// minute instant already judged against our own clock, and when we processed it.
//
// The bucket instant is fixed here rather than recomputed when the span
// eventually settles, so a span's minute is decided by the moment it arrived
// and not by how long its partner kept it waiting.
type observedSpan struct {
	span     Span
	bucketAt time.Time
	arrived  time.Time
}

// pendingSpan is one half of an edge, waiting for the other.
type pendingSpan struct {
	observed observedSpan
	edgeKind string
	isCaller bool
}

// resolution is a caller-side span whose fate is now settled: paired with the
// callee it called, or resolved without one.
//
// A callee-side span that never finds its caller produces no resolution. Its
// caller is genuinely unknown — naming one would be an invention, and the
// callee's own side of the call is already in `endpoint_stats_1m`.
type resolution struct {
	caller   observedSpan
	callee   Span
	edgeKind string
	paired   bool
}

// pairBuffer holds spans that could form an edge until their partner arrives,
// the wait runs out, or the buffer is full.
//
// It needs no lock and no shared store: the receiver keys every span by
// trace_id, so Kafka delivers a whole trace to one partition and one consumer,
// and both halves of an edge are in the same trace.
type pairBuffer struct {
	window time.Duration
	max    int

	entries map[pairKey]pendingSpan
	// arrivals is the parked keys in arrival order, which is expiry order. It
	// holds keys whose entry has since been paired away; those are skipped.
	arrivals []pairKey

	stats pairStats
}

type pairStats struct {
	paired    uint64
	expired   uint64
	evicted   uint64
	displaced uint64
	// ignored counts spans that cannot take part in an edge at all: an
	// unusable span id, or a kind that is neither end of a call.
	ignored uint64
}

func newPairBuffer(window time.Duration, max int) *pairBuffer {
	return &pairBuffer{window: window, max: max, entries: make(map[pairKey]pendingSpan)}
}

// add offers one span to the buffer and returns every caller-side span the
// offer settled — the new pair when it completed one, and any span the new
// arrival pushed out.
func (b *pairBuffer) add(observed observedSpan) []resolution {
	pending, key, ok := pendingFor(observed)
	if !ok {
		b.stats.ignored++
		return nil
	}

	held, occupied := b.entries[key]
	if !occupied {
		return b.park(key, pending)
	}
	if canPair(held, pending) {
		delete(b.entries, key)
		b.stats.paired++
		return []resolution{pairOf(held, pending)}
	}

	// One slot, one span: the span already holding it keeps it, so the outcome
	// does not depend on which of two malformed spans arrived first. The
	// newcomer is settled now instead of waiting for a partner it can no longer
	// reach.
	b.stats.displaced++
	return unpairedResolution(pending)
}

func (b *pairBuffer) park(key pairKey, pending pendingSpan) []resolution {
	var pushedOut []resolution
	if len(b.entries) >= b.max {
		pushedOut = b.evictOldest()
	}
	b.entries[key] = pending
	b.arrivals = append(b.arrivals, key)
	return pushedOut
}

// evictOldest makes room by settling the span that has waited longest. A full
// buffer is a worse graph, never a lost one: an evicted caller still reports the
// call it made.
func (b *pairBuffer) evictOldest() []resolution {
	for len(b.arrivals) > 0 {
		key := b.arrivals[0]
		b.arrivals = b.arrivals[1:]
		pending, ok := b.entries[key]
		if !ok {
			continue // already paired away
		}
		delete(b.entries, key)
		b.stats.evicted++
		return unpairedResolution(pending)
	}
	return nil
}

// expire settles every span that has waited longer than the window.
func (b *pairBuffer) expire(now time.Time) []resolution {
	var settled []resolution
	for len(b.arrivals) > 0 {
		key := b.arrivals[0]
		pending, ok := b.entries[key]
		if ok && now.Sub(pending.observed.arrived) < b.window {
			break
		}
		b.arrivals = b.arrivals[1:]
		if !ok {
			continue // already paired away
		}
		delete(b.entries, key)
		b.stats.expired++
		settled = append(settled, unpairedResolution(pending)...)
	}
	return settled
}

// drainAll settles everything held, however recently it arrived. Shutdown calls
// it: a span still waiting for a partner at shutdown will never get one.
func (b *pairBuffer) drainAll() []resolution {
	var settled []resolution
	for _, key := range b.arrivals {
		pending, ok := b.entries[key]
		if !ok {
			continue
		}
		delete(b.entries, key)
		b.stats.expired++
		settled = append(settled, unpairedResolution(pending)...)
	}
	b.arrivals = b.arrivals[:0]
	return settled
}

func (b *pairBuffer) pending() int { return len(b.entries) }

// pendingFor turns a span into the buffer entry it would park as, or reports
// that it cannot take part in an edge.
func pendingFor(observed observedSpan) (pendingSpan, pairKey, bool) {
	span := observed.span
	if len(span.SpanID) == 0 {
		return pendingSpan{}, pairKey{}, false
	}

	var callerSpanID string
	edgeKind, isCaller := callerKind(span.Kind)
	if isCaller {
		callerSpanID = string(span.SpanID)
	} else {
		var isCallee bool
		if edgeKind, isCallee = calleeKind(span.Kind); !isCallee {
			return pendingSpan{}, pairKey{}, false
		}
		if len(span.ParentSpanID) == 0 {
			return pendingSpan{}, pairKey{}, false // a root span was called by nobody we can see
		}
		callerSpanID = string(span.ParentSpanID)
	}

	key := pairKey{
		tenantID:     span.TenantID,
		projectID:    span.ProjectID,
		traceID:      string(span.TraceID),
		callerSpanID: callerSpanID,
	}
	return pendingSpan{observed: observed, edgeKind: edgeKind, isCaller: isCaller}, key, true
}

// canPair reports whether two spans under one key are the two ends of one call.
// A CLIENT is only answered by a SERVER and a PRODUCER only by a CONSUMER, so a
// mismatched kind is not a pair even when the parent link says so.
func canPair(held, arriving pendingSpan) bool {
	return held.isCaller != arriving.isCaller && held.edgeKind == arriving.edgeKind
}

func pairOf(held, arriving pendingSpan) resolution {
	caller, callee := held, arriving
	if arriving.isCaller {
		caller, callee = arriving, held
	}
	return resolution{
		caller:   caller.observed,
		callee:   callee.observed.span,
		edgeKind: caller.edgeKind,
		paired:   true,
	}
}

// unpairedResolution settles a span that will never find its partner.
//
// A CLIENT still reports its call. The request was made, something answered it
// and the caller waited on the answer, so dropping it would hide a real
// dependency — it is drawn to a reserved, obviously synthetic callee instead of
// to an invented service name.
//
// A PRODUCER does not. A missing CONSUMER is the normal state of a queue, not
// evidence of an uninstrumented one: the message may not be consumed for
// minutes, long after any wait a live ingest path can afford. Reporting one
// would put a fabricated uninstrumented service beside every healthy queue.
func unpairedResolution(pending pendingSpan) []resolution {
	if !pending.isCaller || pending.edgeKind != EdgeCall {
		return nil
	}
	return []resolution{{caller: pending.observed, edgeKind: pending.edgeKind}}
}
