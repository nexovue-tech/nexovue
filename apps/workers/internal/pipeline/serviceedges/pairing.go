package serviceedges

import "time"

// pairKey identifies one caller-side span while the hops out of it are still
// arriving: the trace it belongs to, and its span id inside that trace.
//
// Both ends of a hop meet under the caller's identity — the caller under its
// own, the callee under whichever span it names as its cause — so one lookup
// finds a partner whichever half arrives first.
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
	// seq identifies this parked span for expiry and eviction. A slot holds a
	// caller and any number of callees, and they settle one at a time.
	seq uint64
	// pairedServices are the callee services this caller has already answered.
	// Only a queue caller collects them, because only a queue caller stays
	// parked after it pairs; it is what stops one message being counted twice
	// when a consumer emits both a receive and a process span for it.
	pairedServices []string
}

// slot holds every span parked under one caller identity.
//
// A call has exactly two ends: one client, and the one server that answered it.
// A published message does not. Every consumer group reading the topic gets its
// own copy and each is a separate dependency, so a queue caller stays parked
// after it pairs and its callees are a list, not a single partner.
type slot struct {
	caller  *pendingSpan
	callees []pendingSpan
}

// parkedRef is one parked span in arrival order, which is expiry order. The
// span it names may already have been paired away; those are skipped.
type parkedRef struct {
	key     pairKey
	seq     uint64
	arrived time.Time
}

// resolution is a caller-side span whose fate is now settled: paired with a
// callee it reached, or resolved without one.
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
// A call's two halves are in one trace, so Kafka's trace-id key delivers them
// to one consumer. A queue's two halves are not: the consumer starts a new
// trace, which is keyed onto its own partition. They meet here only while one
// worker holds both partitions — see the package comment.
type pairBuffer struct {
	window time.Duration
	max    int

	entries map[pairKey]*slot
	// arrivals is the parked spans in arrival order, which is expiry order.
	arrivals []parkedRef
	// parked counts spans held, which is what max bounds: one batch consumer
	// can park a span under every producer it links to.
	parked  int
	nextSeq uint64

	stats pairStats
}

type pairStats struct {
	paired    uint64
	expired   uint64
	evicted   uint64
	displaced uint64
	// ignored counts spans that cannot take part in an edge at all: an
	// unusable span id, a kind that is neither end of a hop, or a callee that
	// names no cause.
	ignored uint64
}

func newPairBuffer(window time.Duration, max int) *pairBuffer {
	return &pairBuffer{window: window, max: max, entries: make(map[pairKey]*slot)}
}

// add offers one span to the buffer and returns every caller-side span the
// offer settled — the pairs it completed, and any span the new arrival pushed
// out.
func (b *pairBuffer) add(observed observedSpan) []resolution {
	span := observed.span
	if !usableID(span.SpanID) {
		b.stats.ignored++
		return nil
	}
	if edgeKind, isCaller := callerKind(span.Kind); isCaller {
		return b.addCaller(observed, edgeKind)
	}
	edgeKind, isCallee := calleeKind(span.Kind)
	if !isCallee {
		b.stats.ignored++
		return nil
	}
	causes := callerKeysOf(span, edgeKind)
	if len(causes) == 0 {
		b.stats.ignored++ // called by nobody we can see
		return nil
	}

	var settled []resolution
	for _, key := range causes {
		settled = append(settled, b.addCallee(key, observed, edgeKind)...)
	}
	return settled
}

// callerKeysOf names every caller identity a callee span could close.
//
// The parent link names one, inside the callee's own trace. For a queue hop the
// links name the rest, and usually the only one there is: the OpenTelemetry
// messaging conventions make a link the default correlation between producer
// and consumer, because a span has one parent and a batch consume has many
// producers. Measured against the OpenTelemetry Demo, not one trace held both
// halves of a queue hop.
//
// Links are read for a queue hop only. On a CLIENT/SERVER pair the parent link
// is the convention and a link means something else — a retry naming the
// attempt it repeats, say — and pairing on it would count that call twice.
func callerKeysOf(span Span, edgeKind string) []pairKey {
	keys := make([]pairKey, 0, 1+len(span.Links))
	if usableID(span.ParentSpanID) {
		keys = append(keys, keyFor(span, span.TraceID, span.ParentSpanID))
	}
	if edgeKind != EdgeQueue {
		return keys
	}
	for _, link := range span.Links {
		if !usableID(link.TraceID) || !usableID(link.SpanID) {
			continue
		}
		// A consumer that is both the producer's child and linked to it names one
		// cause twice, and parking under it twice would hold two buffer slots
		// for one message.
		if key := keyFor(span, link.TraceID, link.SpanID); !containsKey(keys, key) {
			keys = append(keys, key)
		}
	}
	return keys
}

func keyFor(span Span, traceID, callerSpanID []byte) pairKey {
	return pairKey{
		tenantID:     span.TenantID,
		projectID:    span.ProjectID,
		traceID:      string(traceID),
		callerSpanID: string(callerSpanID),
	}
}

func containsKey(keys []pairKey, key pairKey) bool {
	for _, existing := range keys {
		if existing == key {
			return true
		}
	}
	return false
}

// addCaller parks a caller under its own identity and answers every callee that
// arrived before it.
func (b *pairBuffer) addCaller(observed observedSpan, edgeKind string) []resolution {
	key := keyFor(observed.span, observed.span.TraceID, observed.span.SpanID)
	pending := pendingSpan{observed: observed, edgeKind: edgeKind, isCaller: true}

	if held := b.entries[key]; held != nil && held.caller != nil {
		// One identity, one caller: the span already holding it keeps it, so a
		// repeated span id cannot change the graph by arrival order. The
		// newcomer settles now rather than waiting for a partner it can no
		// longer be found by.
		b.stats.displaced++
		return unpairedResolution(pending)
	}

	// Room first, then the slot: making room can evict a span out of this very
	// slot, and a slot left empty is deleted.
	pushedOut := b.reserve()
	held := b.slotFor(key)
	pending.seq = b.take(key, observed.arrived)
	held.caller = &pending

	return append(pushedOut, b.answerWaiting(key, held)...)
}

// addCallee pairs a callee with the caller it names, or parks it until that
// caller arrives.
func (b *pairBuffer) addCallee(key pairKey, observed observedSpan, edgeKind string) []resolution {
	pending := pendingSpan{observed: observed, edgeKind: edgeKind}

	if held := b.entries[key]; held != nil && canPair(held.caller, pending) {
		return b.pair(key, held, pending)
	}

	pushedOut := b.reserve()
	held := b.slotFor(key)
	pending.seq = b.take(key, observed.arrived)
	held.callees = append(held.callees, pending)
	return pushedOut
}

func (b *pairBuffer) slotFor(key pairKey) *slot {
	held, ok := b.entries[key]
	if !ok {
		held = &slot{}
		b.entries[key] = held
	}
	return held
}

// answerWaiting pairs a freshly parked caller with every callee already waiting
// under its identity.
func (b *pairBuffer) answerWaiting(key pairKey, held *slot) []resolution {
	var settled []resolution
	for i := 0; i < len(held.callees); {
		callee := held.callees[i]
		if !canPair(held.caller, callee) {
			i++
			continue
		}
		held.callees = append(held.callees[:i], held.callees[i+1:]...)
		b.release(key, held)
		settled = append(settled, b.pair(key, held, callee)...)
		if held.caller == nil {
			break // a call is answered once, and this one has been
		}
	}
	return settled
}

// pair records one hop and retires whichever halves of it are now spent.
func (b *pairBuffer) pair(key pairKey, held *slot, callee pendingSpan) []resolution {
	caller := held.caller
	if caller.edgeKind == EdgeQueue {
		// One message reaches every consumer group, so the producer stays
		// parked — but a service that emits both a receive and a process span
		// for the same message must not be counted as two deliveries.
		if containsService(caller.pairedServices, serviceOf(callee.observed.span)) {
			return nil
		}
		caller.pairedServices = append(caller.pairedServices, serviceOf(callee.observed.span))
	} else {
		held.caller = nil
		b.release(key, held)
	}

	b.stats.paired++
	return []resolution{{
		caller:   caller.observed,
		callee:   callee.observed.span,
		edgeKind: caller.edgeKind,
		paired:   true,
	}}
}

func containsService(services []string, name string) bool {
	for _, existing := range services {
		if existing == name {
			return true
		}
	}
	return false
}

// canPair reports whether a parked caller and an arriving callee are the two
// ends of one hop. A CLIENT is only answered by a SERVER and a PRODUCER only by
// a CONSUMER, so a mismatched kind is not a pair even when the cause says so.
func canPair(caller *pendingSpan, callee pendingSpan) bool {
	return caller != nil && caller.edgeKind == callee.edgeKind
}

// reserve makes room for one more parked span, settling the span that has
// waited longest if the buffer is full. A full buffer is a worse graph, never a
// lost one: an evicted caller still reports the call it made.
func (b *pairBuffer) reserve() []resolution {
	if b.parked < b.max {
		return nil
	}
	for len(b.arrivals) > 0 {
		ref := b.arrivals[0]
		b.arrivals = b.arrivals[1:]
		pending, ok := b.remove(ref)
		if !ok {
			continue // already paired away
		}
		b.stats.evicted++
		return unpairedResolution(pending)
	}
	return nil
}

// take records a parked span's arrival and gives it its identity.
func (b *pairBuffer) take(key pairKey, arrived time.Time) uint64 {
	b.nextSeq++
	b.arrivals = append(b.arrivals, parkedRef{key: key, seq: b.nextSeq, arrived: arrived})
	b.parked++
	return b.nextSeq
}

// release accounts for a span the slot no longer holds, so the buffer's count
// matches what it holds. Its arrival stays in the queue and is skipped there.
func (b *pairBuffer) release(key pairKey, held *slot) {
	b.parked--
	b.discardIfEmpty(key, held)
}

// remove takes the span an arrival names out of the buffer, reporting whether
// it was still there.
func (b *pairBuffer) remove(ref parkedRef) (pendingSpan, bool) {
	held, ok := b.entries[ref.key]
	if !ok {
		return pendingSpan{}, false
	}
	if held.caller != nil && held.caller.seq == ref.seq {
		pending := *held.caller
		held.caller = nil
		b.parked--
		b.discardIfEmpty(ref.key, held)
		return pending, true
	}
	for i, callee := range held.callees {
		if callee.seq != ref.seq {
			continue
		}
		held.callees = append(held.callees[:i], held.callees[i+1:]...)
		b.parked--
		b.discardIfEmpty(ref.key, held)
		return callee, true
	}
	return pendingSpan{}, false
}

func (b *pairBuffer) discardIfEmpty(key pairKey, held *slot) {
	if held.caller == nil && len(held.callees) == 0 {
		delete(b.entries, key)
	}
}

// expire settles every span that has waited longer than the window.
func (b *pairBuffer) expire(now time.Time) []resolution {
	var settled []resolution
	for len(b.arrivals) > 0 {
		ref := b.arrivals[0]
		if b.holds(ref) && now.Sub(ref.arrived) < b.window {
			break
		}
		b.arrivals = b.arrivals[1:]
		pending, ok := b.remove(ref)
		if !ok {
			continue // already paired away
		}
		b.stats.expired++
		settled = append(settled, unpairedResolution(pending)...)
	}
	return settled
}

// drainAll settles everything held, however recently it arrived. Shutdown calls
// it: a span still waiting for a partner at shutdown will never get one.
func (b *pairBuffer) drainAll() []resolution {
	var settled []resolution
	for _, ref := range b.arrivals {
		pending, ok := b.remove(ref)
		if !ok {
			continue
		}
		b.stats.expired++
		settled = append(settled, unpairedResolution(pending)...)
	}
	b.arrivals = b.arrivals[:0]
	return settled
}

// holds reports whether the span an arrival names is still parked.
func (b *pairBuffer) holds(ref parkedRef) bool {
	held, ok := b.entries[ref.key]
	if !ok {
		return false
	}
	if held.caller != nil && held.caller.seq == ref.seq {
		return true
	}
	for _, callee := range held.callees {
		if callee.seq == ref.seq {
			return true
		}
	}
	return false
}

func (b *pairBuffer) pending() int { return b.parked }

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
