package cache

import (
	"testing"
	"time"
)

// row stands in for whatever the callers hold. Only Exists matters here: the
// cache never looks inside a value.
type row struct {
	found bool
}

func (r row) Exists() bool { return r.found }

// A key created seconds ago is exactly where a miss is likely and temporary.
// Holding it as long as a hit refused valid keys for the whole TTL, which is
// the onboarding path: paste the key, export, get 401 for thirty seconds.
func TestAMissIsForgottenLongBeforeAHit(t *testing.T) {
	at := time.Unix(0, 0)
	c := New[row](30*time.Second, 10)
	c.UseClock(func() time.Time { return at })

	c.Store("missing", row{found: false})
	c.Store("present", row{found: true})

	at = at.Add(MissTTL + time.Second)

	if _, cached := c.Lookup("missing"); cached {
		t.Error("the miss is still believed; a key created since would stay refused")
	}
	if _, cached := c.Lookup("present"); !cached {
		t.Error("the hit expired early; a revoked key's window is what that TTL is for")
	}
}

func TestAMissIsCachedLongEnoughToSpareThePostgresLookup(t *testing.T) {
	at := time.Unix(0, 0)
	c := New[row](30*time.Second, 10)
	c.UseClock(func() time.Time { return at })
	c.Store("missing", row{found: false})

	at = at.Add(MissTTL / 2)

	if _, cached := c.Lookup("missing"); !cached {
		t.Error("a repeated bad key reaches the database every time")
	}
}

// A deployment configured below the miss TTL must not accidentally raise it.
func TestAShortConfiguredTTLIsNotLengthenedForMisses(t *testing.T) {
	at := time.Unix(0, 0)
	c := New[row](time.Second, 10)
	c.UseClock(func() time.Time { return at })
	c.Store("missing", row{found: false})

	at = at.Add(1500 * time.Millisecond)

	if _, cached := c.Lookup("missing"); cached {
		t.Error("the miss outlived the configured TTL")
	}
}

func TestTheCacheStaysWithinItsCeiling(t *testing.T) {
	const maxEntries = 8
	c := New[row](time.Minute, maxEntries)

	for i := range 100 {
		c.Store(string(rune('a'+i%26))+string(rune('a'+i/26)), row{found: false})
		if c.Len() > maxEntries {
			t.Fatalf("cache grew to %d entries, want at most %d: misses are attacker-chosen",
				c.Len(), maxEntries)
		}
	}
}

// A hit that has aged past the TTL is gone, which is what bounds how long a
// revoked credential or a spent allowance keeps being believed.
func TestAHitExpiresWithTheConfiguredTTL(t *testing.T) {
	at := time.Unix(0, 0)
	c := New[row](30*time.Second, 10)
	c.UseClock(func() time.Time { return at })
	c.Store("present", row{found: true})

	at = at.Add(31 * time.Second)

	if _, cached := c.Lookup("present"); cached {
		t.Error("a hit outlived its TTL; nothing would ever be re-read")
	}
}

// Group 9, C1: when the control plane cannot answer, a row believed a moment ago
// is better than refusing a customer's steady traffic — for a bounded while.
func TestAnExpiredHitIsStaleForTheWindowAndNoLonger(t *testing.T) {
	at := time.Unix(0, 0)
	c := New[row](30*time.Second, 10, WithMaxStale[row](10*time.Minute))
	c.UseClock(func() time.Time { return at })
	c.Store("present", row{found: true})

	at = at.Add(30*time.Second + 9*time.Minute)
	if _, ok := c.Stale("present"); !ok {
		t.Error("a hit inside the stale window was not offered")
	}

	at = at.Add(2 * time.Minute)
	if _, ok := c.Stale("present"); ok {
		t.Error("a hit past the stale window was still offered")
	}
}

func TestAMissIsNeverStale(t *testing.T) {
	at := time.Unix(0, 0)
	c := New[row](30*time.Second, 10, WithMaxStale[row](10*time.Minute))
	c.UseClock(func() time.Time { return at })
	c.Store("missing", row{found: false})

	at = at.Add(MissTTL + time.Second)
	if _, ok := c.Stale("missing"); ok {
		t.Error("a stale miss would refuse a key created since, for the whole window")
	}
}

// Misses are attacker-chosen. Filling the cache with them must evict other
// misses, never the customers' keys.
func TestAFloodOfMissesLeavesTheHits(t *testing.T) {
	c := New[row](30*time.Second, 10)
	c.Store("customer", row{found: true})
	for i := range 50 {
		c.Store(string(rune('a'+i%26))+string(rune('A'+i/26)), row{found: false})
	}

	if _, ok := c.Lookup("customer"); !ok {
		t.Fatal("a flood of invented keys evicted a customer's")
	}
	if c.Len() > 10 {
		t.Fatalf("the cache holds %d, past its ceiling of 10", c.Len())
	}
}
