package ratelimit

import (
	"sync"
	"time"
)

// The bound the door falls back to when the shared counter cannot answer at all
// (ADR 0104). The door stays open on a Valkey error — losing a customer's
// telemetry costs more than briefly losing the protection — and this is what
// stops "open" meaning "unbounded" for the length of the outage.
//
// **It is per receiver instance, and that is stated rather than glossed.** The
// effective ceiling is FallbackPerSecond times however many receivers are
// running, and it grows as the fleet grows. An in-process counter cannot be
// otherwise, and a comment implying a cluster-wide limit is how a fallback
// becomes a lie.

// fallbackBurstMultiple sizes the in-process bound as a multiple of the
// configured per-second limit rather than equal to it.
//
// Equal would be wrong in the one direction that matters: each instance can
// only see its own share of a key's traffic, so an instance holding a tenth of
// the fleet's requests would start refusing at a tenth of the configured limit,
// on every instance at once, for traffic Valkey would have allowed. The
// fallback exists to stop a burst large enough to hurt every other tenant, not
// to reproduce a limit it cannot see the rest of.
//
// Provisional: ADR 0104 says the multiple is a number somebody watches rather
// than a number somebody picks, and what would revise it is a measured outage.
const fallbackBurstMultiple = 10

// fallbackMaxSubjects bounds the map, for the reason `cache.go` already gives:
// the key prefix on an invalid request is attacker-chosen, so an unbounded map
// is an unbounded memory cost handed to whoever can reach the port. One entry
// is a string and two words, so this ceiling is small in bytes and large in
// distinct keys.
const fallbackMaxSubjects = 10_000

// minimumFallbackPerSecond is what a nonsensical limit becomes. `config.validate`
// requires RECEIVER_RATE_LIMIT_PER_SECOND to be positive, so this is not a
// configuration the receiver boots with — but this package is not the place to
// divide by zero, and one token a second is the smallest bound that can still
// answer.
const minimumFallbackPerSecond = 1

// tokenBucket is one subject's allowance in this process: tokens that refill at
// a fixed rate and cap at one second's worth.
type tokenBucket struct {
	tokens   float64
	refilled time.Time
}

// inProcessLimiter is a token bucket per subject, bounded in both directions —
// perSecond tokens a second for each subject, and at most maxSubjects subjects.
//
// One mutex over the whole map rather than a lock per bucket: this path runs
// only while the shared counter is unreachable, and a simpler structure is
// worth more there than a contention win nobody has measured.
type inProcessLimiter struct {
	mu          sync.Mutex
	buckets     map[string]*tokenBucket
	perSecond   float64
	maxSubjects int
}

func newInProcessLimiter(perSecond int64, maxSubjects int) *inProcessLimiter {
	if perSecond < minimumFallbackPerSecond {
		perSecond = minimumFallbackPerSecond
	}
	return &inProcessLimiter{
		buckets:     make(map[string]*tokenBucket),
		perSecond:   float64(perSecond),
		maxSubjects: maxSubjects,
	}
}

// allow spends one token for subject, reporting whether there was one.
//
// A subject seen for the first time starts full, so an outage does not begin by
// refusing everybody: the bucket is a burst allowance, not a debt.
func (l *inProcessLimiter) allow(subject string, now time.Time) Decision {
	l.mu.Lock()
	defer l.mu.Unlock()

	held, known := l.buckets[subject]
	if !known {
		if len(l.buckets) >= l.maxSubjects {
			l.makeRoom(now)
		}
		held = &tokenBucket{tokens: l.perSecond, refilled: now}
		l.buckets[subject] = held
	}
	held.refill(now, l.perSecond)

	if held.tokens < 1 {
		return Decision{Allowed: false, RetryAfter: held.waitForOneToken(l.perSecond)}
	}
	held.tokens--
	return Decision{Allowed: true}
}

// makeRoom drops the buckets that have refilled to full — a full bucket answers
// exactly as a fresh one would, so forgetting it costs nothing — and, if that
// was not enough, everything. Emptying is bounded, rare, and far simpler to
// reason about than an eviction policy an attacker can steer, which is the same
// choice `cache.go` made for the same reason.
func (l *inProcessLimiter) makeRoom(now time.Time) {
	for subject, held := range l.buckets {
		held.refill(now, l.perSecond)
		if held.tokens >= l.perSecond {
			delete(l.buckets, subject)
		}
	}
	if len(l.buckets) >= l.maxSubjects {
		clear(l.buckets)
	}
}

// refill credits the tokens the elapsed time earned, capped at one second's
// worth so an idle subject cannot bank a burst it was never entitled to.
//
// Time that has not moved forward earns nothing and moves no clock: a clock
// that steps backwards must not hand out credit, and it must not be recorded as
// the last refill either, or the step is paid for twice.
func (b *tokenBucket) refill(now time.Time, perSecond float64) {
	elapsed := now.Sub(b.refilled)
	if elapsed <= 0 {
		return
	}
	b.tokens = min(perSecond, b.tokens+elapsed.Seconds()*perSecond)
	b.refilled = now
}

// waitForOneToken is how long until this bucket has earned a whole token, which
// is what a refused caller is told to wait.
func (b *tokenBucket) waitForOneToken(perSecond float64) time.Duration {
	return time.Duration((1 - b.tokens) / perSecond * float64(time.Second))
}
