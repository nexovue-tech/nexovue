package ratelimit

import (
	"context"
	"testing"
	"time"
)

// atInstant pins the limiter's clock so the bucket's arithmetic is provable
// without sleeping.
func atInstant(limiter *Limiter, now *time.Time) {
	limiter.now = func() time.Time { return *now }
}

// The bound is a multiple of the configured limit, not the limit itself: each
// instance sees only its share of a key's traffic, so matching the configured
// number would refuse traffic Valkey would have allowed, on every instance at
// once, for the length of the outage.
func TestTheFallbackBoundIsAMultipleOfTheConfiguredLimit(t *testing.T) {
	const configured = 1000
	limiter := New(newFakeCounter(), configured)

	if got := limiter.FallbackPerSecond(); got != configured*fallbackBurstMultiple {
		t.Errorf("FallbackPerSecond() = %d, want %d", got, configured*fallbackBurstMultiple)
	}
	if limiter.FallbackPerSecond() <= configured {
		t.Error("the fallback must be looser than the shared limit, not a copy of it")
	}
}

// The whole behaviour in one test: while the counter is down a key gets the
// fallback's burst and nothing more.
func TestTheFallbackAllowsItsBurstAndThenRefuses(t *testing.T) {
	const configured = 2
	burst := configured * fallbackBurstMultiple
	limiter := New(newFakeCounter(), configured)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	for attempt := 1; attempt <= burst; attempt++ {
		if decision := limiter.AllowWhileCounterDown("prefix"); !decision.Allowed {
			t.Fatalf("request %d of %d was refused, want allowed", attempt, burst)
		}
	}

	decision := limiter.AllowWhileCounterDown("prefix")
	if decision.Allowed {
		t.Fatalf("request %d was allowed; the in-process bound is %d", burst+1, burst)
	}
	if decision.RetryAfter <= 0 {
		t.Errorf("RetryAfter = %v, want a positive wait: a refusal has to say how long",
			decision.RetryAfter)
	}
}

// Tokens come back with time, so an outage that lasts does not permanently
// silence a key that slowed down.
func TestTheFallbackRefillsWithTime(t *testing.T) {
	const configured = 1
	burst := configured * fallbackBurstMultiple
	limiter := New(newFakeCounter(), configured)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	for range burst {
		limiter.AllowWhileCounterDown("prefix")
	}
	if limiter.AllowWhileCounterDown("prefix").Allowed {
		t.Fatal("the burst was not spent")
	}

	now = now.Add(time.Second)

	if !limiter.AllowWhileCounterDown("prefix").Allowed {
		t.Error("a second later the bucket has refilled and must allow again")
	}
}

// An idle key must not bank a burst it was never entitled to: a key silent for
// an hour still gets one second's worth, not an hour's.
func TestTheFallbackDoesNotBankAnUnusedBurst(t *testing.T) {
	const configured = 1
	burst := configured * fallbackBurstMultiple
	limiter := New(newFakeCounter(), configured)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	limiter.AllowWhileCounterDown("prefix")
	now = now.Add(time.Hour)

	allowed := 0
	for range burst * 4 {
		if limiter.AllowWhileCounterDown("prefix").Allowed {
			allowed++
		}
	}

	if allowed != burst {
		t.Errorf("allowed %d after an idle hour, want %d: the bucket caps at one second",
			allowed, burst)
	}
}

// One key's burst is not another's. A noisy tenant during an outage must not
// spend a quiet tenant's fallback.
func TestTheFallbackKeepsSubjectsApart(t *testing.T) {
	const configured = 1
	limiter := New(newFakeCounter(), configured)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	for range configured * fallbackBurstMultiple {
		limiter.AllowWhileCounterDown("noisy")
	}
	if limiter.AllowWhileCounterDown("noisy").Allowed {
		t.Fatal("the noisy key's burst was not spent")
	}

	if !limiter.AllowWhileCounterDown("quiet").Allowed {
		t.Error("a second key was refused for the first key's traffic")
	}
}

// The map is bounded, for the reason cache.go gives: the key prefix on an
// invalid request is attacker-chosen, so an unbounded map is an unbounded
// memory cost handed to whoever can reach the port.
func TestTheFallbackMapIsBounded(t *testing.T) {
	const maxSubjects = 8
	limiter := newInProcessLimiter(4, maxSubjects)
	now := fixedTime(t, "2026-09-07T10:00:00Z")

	for attempt := range maxSubjects * 50 {
		limiter.allow(string(rune('a'+attempt%26))+string(rune(attempt)), 1, now)
		if len(limiter.buckets) > maxSubjects {
			t.Fatalf("held %d buckets after %d distinct keys, want at most %d",
				len(limiter.buckets), attempt+1, maxSubjects)
		}
	}
}

// A limit this package cannot divide by is not a configuration the receiver
// boots with — config.validate refuses it — but arithmetic on it must not be
// undefined here either.
func TestTheFallbackSurvivesANonsensicalLimit(t *testing.T) {
	limiter := New(newFakeCounter(), 0)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	if got := limiter.FallbackPerSecond(); got < minimumFallbackPerSecond {
		t.Errorf("FallbackPerSecond() = %d, want at least %d", got, minimumFallbackPerSecond)
	}
	if !limiter.AllowWhileCounterDown("prefix").Allowed {
		t.Error("the first request must still be answerable")
	}
	if decision := limiter.AllowWhileCounterDown("prefix"); decision.Allowed {
		t.Error("a limit of one a second allows one a second")
	} else if decision.RetryAfter <= 0 || decision.RetryAfter > time.Second {
		t.Errorf("RetryAfter = %v, want a wait inside one second", decision.RetryAfter)
	}
}

// A clock that steps backwards must not hand out credit, and must not have the
// step charged twice when it comes forward again.
func TestTheFallbackIgnoresTimeGoingBackwards(t *testing.T) {
	const configured = 1
	burst := configured * fallbackBurstMultiple
	limiter := New(newFakeCounter(), configured)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	for range burst {
		limiter.AllowWhileCounterDown("prefix")
	}

	now = now.Add(-time.Hour)

	if limiter.AllowWhileCounterDown("prefix").Allowed {
		t.Error("time going backwards refilled the bucket")
	}
}

// The fallback is for the error path only. A healthy counter answers on its
// own, and nothing about the fallback may change that answer.
func TestAHealthyCounterNeverTouchesTheFallback(t *testing.T) {
	const configured = 2
	counter := newFakeCounter()
	limiter := New(counter, configured)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	for attempt := 1; attempt <= configured; attempt++ {
		decision, err := limiter.Allow(context.Background(), "prefix")
		if err != nil || !decision.Allowed {
			t.Fatalf("request %d: decision = %+v, err = %v", attempt, decision, err)
		}
	}

	decision, err := limiter.Allow(context.Background(), "prefix")
	if err != nil {
		t.Fatalf("Allow() = %v, want no error", err)
	}
	if decision.Allowed {
		t.Error("the shared limit was exceeded and the request was allowed anyway")
	}
	if len(limiter.fallback.buckets) != 0 {
		t.Errorf("the fallback holds %d buckets; a healthy counter must not engage it",
			len(limiter.fallback.buckets))
	}
}
