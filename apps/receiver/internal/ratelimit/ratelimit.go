// Package ratelimit caps how fast one ingest key may send.
//
// The window is a fixed second: one counter per key per second, incremented and
// expired in a single round trip. The control plane's sliding-window log is
// deliberately not reused here — its per-request bookkeeping costs more than
// the span batch it is protecting, and at the ingest edge the cheap answer is
// the right one.
package ratelimit

import (
	"context"
	"fmt"
	"strconv"
	"time"
)

// keyNamespace prefixes every counter, so ingest limits are recognisable in a
// database Valkey shares with caches and live-update channels.
const keyNamespace = "ratelimit:ingest:"

// window is the fixed accounting period. One second is what makes a counter a
// single increment instead of a sorted set.
const window = time.Second

// counterTTL outlives the window it counts by one period, which is enough to
// survive clock jitter and short enough that keys never pile up.
const counterTTL = 2 * window

// Counter is the single operation the limiter needs from its store.
type Counter interface {
	// Increment adds one to key and returns the new count, giving the key ttl
	// if it does not already have one.
	Increment(ctx context.Context, key string, ttl time.Duration) (int64, error)
}

// Decision is the answer for one request.
type Decision struct {
	Allowed bool
	// RetryAfter is how long until the current window rolls over. Only
	// meaningful when the request was refused.
	RetryAfter time.Duration
}

// Limiter counts requests per key.
type Limiter struct {
	counter   Counter
	perSecond int64
	now       func() time.Time // replaced in tests; time.Now in production
}

// New returns a limiter allowing perSecond requests for each subject.
func New(counter Counter, perSecond int) *Limiter {
	return &Limiter{counter: counter, perSecond: int64(perSecond), now: time.Now}
}

// Allow counts one request against subject's current window.
//
// The subject is the key, never the caller's address: one address is one
// customer for server traffic and one visitor for browser traffic, so counting
// addresses would stop bounding the thing the limit exists to bound. See ADR
// 0087.
//
// An error means the counter could not be read; the caller decides what to do
// about that, because whether ingest fails open or closed is a policy question
// and not this package's to answer.
func (l *Limiter) Allow(ctx context.Context, subject string) (Decision, error) {
	now := l.now()
	key := counterKey(subject, now)

	count, err := l.counter.Increment(ctx, key, counterTTL)
	if err != nil {
		return Decision{}, fmt.Errorf("counting %s: %w", key, err)
	}
	if count > l.perSecond {
		return Decision{Allowed: false, RetryAfter: untilNextWindow(now)}, nil
	}
	return Decision{Allowed: true}, nil
}

// counterKey names one subject's window. The window number is part of the key
// rather than a stored value, so rolling over costs nothing.
func counterKey(subject string, now time.Time) string {
	return keyNamespace + subject + ":" + strconv.FormatInt(now.Unix(), 10)
}

func untilNextWindow(now time.Time) time.Duration {
	return window - time.Duration(now.UnixNano())%window
}
