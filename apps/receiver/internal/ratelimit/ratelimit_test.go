package ratelimit

import (
	"testing"
	"time"
)

func fixedTime(t *testing.T, value string) time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		t.Fatalf("parsing %q: %v", value, err)
	}
	return parsed
}

// atInstant pins the limiter's clock so the bucket's arithmetic is provable
// without sleeping.
func atInstant(limiter *Limiter, now *time.Time) {
	limiter.now = func() time.Time { return *now }
}

func TestAllowPermitsUpToTheLimitAndRefusesAfterIt(t *testing.T) {
	const limit = 3
	limiter := New(limit, 1)
	now := fixedTime(t, "2026-08-27T10:00:00Z")
	atInstant(limiter, &now)

	for attempt := 1; attempt <= limit; attempt++ {
		if !limiter.Allow("prefix").Allowed {
			t.Fatalf("request %d of %d was refused, want allowed", attempt, limit)
		}
	}

	decision := limiter.Allow("prefix")
	if decision.Allowed {
		t.Fatalf("request %d was allowed; the limit is %d", limit+1, limit)
	}
	if decision.RetryAfter <= 0 || decision.RetryAfter > time.Second {
		t.Errorf("RetryAfter = %v, want a wait inside one second", decision.RetryAfter)
	}
}

// Each receiver holds its share: three receivers each allow a third, so the
// fleet together allows the configured rate.
func TestEachReceiverEnforcesItsShare(t *testing.T) {
	limiter := New(300, 3)
	now := fixedTime(t, "2026-08-27T10:00:00Z")
	atInstant(limiter, &now)

	if got := limiter.PerSecond(); got != 100 {
		t.Fatalf("PerSecond() = %d, want 100: a third of 300", got)
	}
	if !limiter.AllowN("prefix", 100).Allowed {
		t.Fatal("this receiver's whole share was refused")
	}
	if limiter.Allow("prefix").Allowed {
		t.Error("allowed past this receiver's share")
	}
}

// A share smaller than one, and a limit this package cannot divide by, are not
// configurations the receiver boots with — config.validate refuses them — but
// arithmetic on them must not be undefined here either.
func TestAShareIsNeverZero(t *testing.T) {
	for _, limiter := range []*Limiter{New(2, 10), New(0, 1), New(5, 0)} {
		if got := limiter.PerSecond(); got < minimumPerSecond {
			t.Errorf("PerSecond() = %d, want at least %d", got, minimumPerSecond)
		}
	}
}

// Tokens come back with time, so a key that slowed down is not silenced.
func TestTheBucketRefillsWithTime(t *testing.T) {
	limiter := New(1, 1)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	limiter.Allow("prefix")
	if limiter.Allow("prefix").Allowed {
		t.Fatal("the burst was not spent")
	}

	now = now.Add(time.Second)

	if !limiter.Allow("prefix").Allowed {
		t.Error("a second later the bucket has refilled and must allow again")
	}
}

// A token bucket, not a fixed window: a key cannot spend a full second at the end
// of one second and another full second at the start of the next.
func TestABurstAcrossASecondBoundaryIsStillOneSecondsWorth(t *testing.T) {
	limiter := New(10, 1)
	now := fixedTime(t, "2026-09-07T10:00:00.900Z")
	atInstant(limiter, &now)
	limiter.AllowN("prefix", 10)

	now = fixedTime(t, "2026-09-07T10:00:01.000Z")

	if limiter.AllowN("prefix", 10).Allowed {
		t.Error("a second second's worth was allowed a tenth of a second later")
	}
}

// An idle key must not bank a burst it was never entitled to: a key silent for
// an hour still gets one second's worth, not an hour's.
func TestAnIdleKeyDoesNotBankABurst(t *testing.T) {
	const perSecond = 4
	limiter := New(perSecond, 1)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	limiter.Allow("prefix")
	now = now.Add(time.Hour)

	allowed := 0
	for range perSecond * 4 {
		if limiter.Allow("prefix").Allowed {
			allowed++
		}
	}
	if allowed != perSecond {
		t.Errorf("allowed %d after an idle hour, want %d: the bucket caps at one second",
			allowed, perSecond)
	}
}

// One key's burst is not another's: a noisy tenant must not spend a quiet one's.
func TestKeysAreKeptApart(t *testing.T) {
	limiter := New(1, 1)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	limiter.Allow("noisy")
	if limiter.Allow("noisy").Allowed {
		t.Fatal("the noisy key's burst was not spent")
	}
	if !limiter.Allow("quiet").Allowed {
		t.Error("a second key was refused for the first key's traffic")
	}
}

// The map is bounded: the key prefix on an invalid request is attacker-chosen.
func TestTheMapIsBounded(t *testing.T) {
	limiter := New(4, 1)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)

	for attempt := range maxSubjects * 3 {
		limiter.Allow(string(rune(attempt)))
		if len(limiter.buckets) > maxSubjects {
			t.Fatalf("held %d buckets after %d distinct keys, want at most %d",
				len(limiter.buckets), attempt+1, maxSubjects)
		}
	}
}

// A clock that steps backwards must not hand out credit.
func TestTimeGoingBackwardsEarnsNothing(t *testing.T) {
	limiter := New(1, 1)
	now := fixedTime(t, "2026-09-07T10:00:00Z")
	atInstant(limiter, &now)
	limiter.Allow("prefix")

	now = now.Add(-time.Hour)

	if limiter.Allow("prefix").Allowed {
		t.Error("time going backwards refilled the bucket")
	}
}

// AllowN is the same allowance counted in what requests carry — profile samples.
func TestAllowNSpendsWhatRequestsCarry(t *testing.T) {
	limiter := New(100, 1)
	now := fixedTime(t, "2026-08-27T10:00:00Z")
	atInstant(limiter, &now)

	if !limiter.AllowN("prefix", 60).Allowed {
		t.Fatal("60 of a 100 budget was refused")
	}
	if limiter.AllowN("prefix", 60).Allowed {
		t.Error("120 of a 100 budget was allowed")
	}
	now = now.Add(time.Second)
	if !limiter.AllowN("prefix", 60).Allowed {
		t.Error("a second later the budget had not refilled")
	}
}

// Refusing a request larger than the whole budget would refuse it for ever; the
// sender cannot make it smaller. It goes through from a full bucket, and leaves
// at most one second of debt.
func TestARequestLargerThanTheBudgetIsAdmittedFromAFullBucket(t *testing.T) {
	limiter := New(100, 1)
	now := fixedTime(t, "2026-08-27T10:00:00Z")
	atInstant(limiter, &now)

	if !limiter.AllowN("prefix", 1_000_000).Allowed {
		t.Fatal("an oversized request to a full bucket was refused")
	}
	if limiter.Allow("prefix").Allowed {
		t.Error("the bucket it spent still admitted more")
	}

	now = now.Add(2 * time.Second)

	if !limiter.Allow("prefix").Allowed {
		t.Error("two seconds after, still refused: the debt must be at most one second")
	}
}
