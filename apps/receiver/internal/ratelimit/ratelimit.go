// Package ratelimit caps how fast one ingest key may send.
//
// **Counted in this process** (ADR-020). Each receiver enforces its share of the
// limit — the configured rate divided by RECEIVER_INSTANCES — with a token bucket
// per key, so no shared store sits on the ingest path. On one receiver that is the
// limit exactly; across several it is exact when a key's traffic spreads evenly
// over them, which a load balancer in front of the fleet arranges, and the
// founder accepted the rest.
//
// A token bucket rather than a fixed window: it refills continuously, so a key
// cannot spend a full second's allowance at the end of one second and another at
// the start of the next.
package ratelimit

import (
	"sync"
	"time"
)

// maxSubjects bounds the map. The key prefix on an invalid request is
// attacker-chosen, so an unbounded map is an unbounded memory cost handed to
// whoever can reach the port. One entry is a string and two words, so this is
// small in bytes and large in distinct keys.
const maxSubjects = 10_000

// minimumPerSecond is what a share smaller than one becomes: the smallest bound
// that can still answer.
const minimumPerSecond = 1

// Decision is the answer for one request.
type Decision struct {
	Allowed bool
	// RetryAfter is how long until the subject has earned what it asked for.
	// Only meaningful when the request was refused.
	RetryAfter time.Duration
}

// tokenBucket is one subject's allowance: tokens that refill at a fixed rate and
// cap at one second's worth.
type tokenBucket struct {
	tokens   float64
	refilled time.Time
}

// Limiter counts requests per subject — or, through AllowN, anything else a
// request carries, such as profile samples.
//
// One mutex over the whole map rather than a lock per bucket: the critical
// section is a few arithmetic operations, and nobody has measured contention
// that would justify more structure.
type Limiter struct {
	mu        sync.Mutex
	buckets   map[string]*tokenBucket
	perSecond float64

	now func() time.Time // replaced in tests; time.Now in production
}

// New returns a limiter allowing this receiver's share — perSecond divided by
// instances — for each subject.
func New(perSecond, instances int) *Limiter {
	share := perSecond / max(instances, 1)
	return &Limiter{
		buckets:   make(map[string]*tokenBucket),
		perSecond: float64(max(share, minimumPerSecond)),
		now:       time.Now,
	}
}

// PerSecond is what this receiver allows each subject, after its share is taken.
func (l *Limiter) PerSecond() int64 { return int64(l.perSecond) }

// Allow counts one request against subject.
//
// The subject is the key, never the caller's address: one address is one
// customer for server traffic and one visitor for browser traffic, so counting
// addresses would stop bounding the thing the limit exists to bound.
func (l *Limiter) Allow(subject string) Decision {
	return l.AllowN(subject, 1)
}

// AllowN spends n of subject's tokens, for a limit on what requests carry rather
// than on how many there are.
//
// A subject seen for the first time starts full, so the limit is a burst
// allowance and not a debt. A cost larger than a full bucket is admitted from a
// full one, and leaves at most one second of debt: refusing it would refuse it for
// ever, and the sender would retry a request nothing can make smaller.
func (l *Limiter) AllowN(subject string, n int64) Decision {
	l.mu.Lock()
	defer l.mu.Unlock()

	now := l.now()
	held, known := l.buckets[subject]
	if !known {
		if len(l.buckets) >= maxSubjects {
			l.makeRoom(now)
		}
		held = &tokenBucket{tokens: l.perSecond, refilled: now}
		l.buckets[subject] = held
	}
	held.refill(now, l.perSecond)

	cost := float64(n)
	needed := min(cost, l.perSecond)
	if held.tokens < needed {
		return Decision{Allowed: false, RetryAfter: held.waitFor(needed, l.perSecond)}
	}
	held.tokens = max(held.tokens-cost, -l.perSecond)
	return Decision{Allowed: true}
}

// makeRoom drops the buckets that have refilled to full — a full bucket answers
// exactly as a fresh one would, so forgetting it costs nothing — and, if that was
// not enough, everything. Emptying is bounded, rare, and far simpler to reason
// about than an eviction policy an attacker can steer, which is the same choice
// `cache.go` made for the same reason.
func (l *Limiter) makeRoom(now time.Time) {
	for subject, held := range l.buckets {
		held.refill(now, l.perSecond)
		if held.tokens >= l.perSecond {
			delete(l.buckets, subject)
		}
	}
	if len(l.buckets) >= maxSubjects {
		clear(l.buckets)
	}
}

// refill credits the tokens the elapsed time earned, capped at one second's
// worth so an idle subject cannot bank a burst it was never entitled to.
//
// Time that has not moved forward earns nothing and moves no clock: a clock that
// steps backwards must not hand out credit, and it must not be recorded as the
// last refill either, or the step is paid for twice.
func (b *tokenBucket) refill(now time.Time, perSecond float64) {
	elapsed := now.Sub(b.refilled)
	if elapsed <= 0 {
		return
	}
	b.tokens = min(perSecond, b.tokens+elapsed.Seconds()*perSecond)
	b.refilled = now
}

// waitFor is how long until this bucket has earned the tokens a refused caller
// needs, which is what it is told to wait.
func (b *tokenBucket) waitFor(needed, perSecond float64) time.Duration {
	return time.Duration((needed - b.tokens) / perSecond * float64(time.Second))
}
