package ratelimit

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

// fakeCounter records what the limiter asked for and answers with a running
// count, so the window arithmetic can be tested without a Valkey instance.
type fakeCounter struct {
	counts map[string]int64
	ttls   map[string]time.Duration
	err    error
}

func newFakeCounter() *fakeCounter {
	return &fakeCounter{counts: map[string]int64{}, ttls: map[string]time.Duration{}}
}

func (f *fakeCounter) Increment(_ context.Context, key string, by int64, ttl time.Duration) (int64, error) {
	if f.err != nil {
		return 0, f.err
	}
	f.counts[key] += by
	f.ttls[key] = ttl
	return f.counts[key], nil
}

func fixedTime(t *testing.T, value string) time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		t.Fatalf("parsing %q: %v", value, err)
	}
	return parsed
}

func TestAllowPermitsUpToTheLimitAndRefusesAfterIt(t *testing.T) {
	const limit = 3
	counter := newFakeCounter()
	limiter := New(counter, limit)
	limiter.now = func() time.Time { return fixedTime(t, "2026-08-27T10:00:00Z") }

	for attempt := 1; attempt <= limit; attempt++ {
		decision, err := limiter.Allow(context.Background(), "prefix")
		if err != nil {
			t.Fatalf("Allow() = %v, want no error", err)
		}
		if !decision.Allowed {
			t.Fatalf("request %d of %d was refused, want allowed", attempt, limit)
		}
	}

	decision, err := limiter.Allow(context.Background(), "prefix")
	if err != nil {
		t.Fatalf("Allow() = %v, want no error", err)
	}
	if decision.Allowed {
		t.Error("the request past the limit was allowed")
	}
	if decision.RetryAfter <= 0 {
		t.Error("a refused request must say how long to wait")
	}
}

func TestAllowStartsAFreshCountEachSecond(t *testing.T) {
	counter := newFakeCounter()
	limiter := New(counter, 1)

	now := fixedTime(t, "2026-08-27T10:00:00.900Z")
	limiter.now = func() time.Time { return now }

	if decision, _ := limiter.Allow(context.Background(), "prefix"); !decision.Allowed {
		t.Fatal("the first request of a window was refused")
	}
	if decision, _ := limiter.Allow(context.Background(), "prefix"); decision.Allowed {
		t.Fatal("the second request of a one-per-second window was allowed")
	}

	now = now.Add(200 * time.Millisecond) // crosses the second boundary
	if decision, _ := limiter.Allow(context.Background(), "prefix"); !decision.Allowed {
		t.Error("the next window did not start with an empty count")
	}
	if len(counter.counts) != 2 {
		t.Errorf("used %d counters, want one per window", len(counter.counts))
	}
}

func TestAllowKeepsSubjectsApart(t *testing.T) {
	counter := newFakeCounter()
	limiter := New(counter, 1)
	limiter.now = func() time.Time { return fixedTime(t, "2026-08-27T10:00:00Z") }

	if decision, _ := limiter.Allow(context.Background(), "first"); !decision.Allowed {
		t.Fatal("the first subject's only request was refused")
	}
	if decision, _ := limiter.Allow(context.Background(), "second"); !decision.Allowed {
		t.Error("one subject's traffic consumed another subject's budget")
	}
}

func TestAllowNamesAndExpiresItsCounters(t *testing.T) {
	counter := newFakeCounter()
	limiter := New(counter, 10)
	limiter.now = func() time.Time { return fixedTime(t, "2026-08-27T10:00:00Z") }

	if _, err := limiter.Allow(context.Background(), "prefix"); err != nil {
		t.Fatalf("Allow() = %v, want no error", err)
	}
	for key, ttl := range counter.ttls {
		if !strings.HasPrefix(key, keyNamespace) {
			t.Errorf("counter key %q does not start with %q", key, keyNamespace)
		}
		if ttl <= window {
			t.Errorf("counter TTL %s does not outlive its %s window", ttl, window)
		}
	}
}

func TestAllowReportsACounterFailure(t *testing.T) {
	unreachable := errors.New("dial tcp: connection refused")
	limiter := New(&fakeCounter{err: unreachable}, 10)

	if _, err := limiter.Allow(context.Background(), "prefix"); !errors.Is(err, unreachable) {
		t.Fatalf("Allow() = %v, want the counter failure so the caller can decide", err)
	}
}

func TestRetryAfterCoversTheRestOfTheWindow(t *testing.T) {
	cases := map[string]struct {
		at   string
		want time.Duration
	}{
		"start of a window":  {"2026-08-27T10:00:00.000Z", time.Second},
		"middle of a window": {"2026-08-27T10:00:00.250Z", 750 * time.Millisecond},
		"end of a window":    {"2026-08-27T10:00:00.999Z", time.Millisecond},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := untilNextWindow(fixedTime(t, tc.at)); got != tc.want {
				t.Errorf("untilNextWindow() = %s, want %s", got, tc.want)
			}
		})
	}
}

// AllowN is the same window counted in what requests carry — profile samples.
func TestAllowNSpendsTheWindowInWhatRequestsCarry(t *testing.T) {
	limiter := New(newFakeCounter(), 100)
	limiter.now = func() time.Time { return fixedTime(t, "2026-08-27T10:00:00Z") }

	if decision, _ := limiter.AllowN(context.Background(), "prefix", 60); !decision.Allowed {
		t.Fatal("60 of a 100 budget was refused")
	}
	if decision, _ := limiter.AllowN(context.Background(), "prefix", 60); decision.Allowed {
		t.Error("120 of a 100 budget was allowed")
	}
	limiter.now = func() time.Time { return fixedTime(t, "2026-08-27T10:00:01Z") }
	if decision, _ := limiter.AllowN(context.Background(), "prefix", 60); !decision.Allowed {
		t.Error("the next second's budget was not fresh")
	}
}

// Refusing a request larger than the whole budget would refuse it for ever; the
// sender cannot make it smaller. It goes through alone, and spends the window.
func TestARequestLargerThanTheBudgetIsAdmittedAloneInAnEmptyWindow(t *testing.T) {
	limiter := New(newFakeCounter(), 100)
	limiter.now = func() time.Time { return fixedTime(t, "2026-08-27T10:00:00Z") }

	if decision, _ := limiter.AllowN(context.Background(), "prefix", 500); !decision.Allowed {
		t.Fatal("a 500-unit request to an empty window was refused")
	}
	if decision, _ := limiter.AllowN(context.Background(), "prefix", 1); decision.Allowed {
		t.Error("the window it spent still admitted more")
	}
}

func TestTheFallbackSpendsTheSameUnits(t *testing.T) {
	limiter := New(newFakeCounter(), 100)
	now := fixedTime(t, "2026-08-27T10:00:00Z")
	limiter.now = func() time.Time { return now }
	budget := float64(limiter.FallbackPerSecond())

	if decision := limiter.AllowNWhileCounterDown("prefix", int64(budget)); !decision.Allowed {
		t.Fatal("a full bucket refused its own size")
	}
	decision := limiter.AllowNWhileCounterDown("prefix", 1)
	if decision.Allowed || decision.RetryAfter <= 0 {
		t.Errorf("an empty bucket answered %+v, want a refusal with a wait", decision)
	}
}

// An oversized request while Valkey is down leaves at most one second of debt, as
// the shared counter spends at most the window it arrived in.
func TestTheFallbacksDebtIsAtMostOneSecond(t *testing.T) {
	limiter := New(newFakeCounter(), 100)
	now := fixedTime(t, "2026-08-27T10:00:00Z")
	limiter.now = func() time.Time { return now }
	if decision := limiter.AllowNWhileCounterDown("prefix", 1_000_000); !decision.Allowed {
		t.Fatal("an oversized request to a full bucket was refused")
	}

	now = now.Add(2 * time.Second)

	if decision := limiter.AllowNWhileCounterDown("prefix", 1); !decision.Allowed {
		t.Errorf("two seconds after, still refused: %+v", decision)
	}
}
