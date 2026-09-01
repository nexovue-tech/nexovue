package auth

import (
	"testing"
	"time"
)

// A key created seconds ago is exactly where a miss is likely and temporary.
// Holding it as long as a hit refused valid keys for the whole TTL, which is
// the onboarding path: paste the key, export, get 401 for thirty seconds.
func TestAMissIsForgottenLongBeforeAHit(t *testing.T) {
	at := time.Unix(0, 0)
	c := newCache[Credential](30*time.Second, 10)
	c.now = func() time.Time { return at }

	c.store("nvk_live_missing", Credential{Found: false})
	c.store("nvk_live_present", Credential{Found: true, KeyHash: "hash"})

	at = at.Add(missTTL + time.Second)

	if _, cached := c.lookup("nvk_live_missing"); cached {
		t.Error("the miss is still believed; a key created since would stay refused")
	}
	if _, cached := c.lookup("nvk_live_present"); !cached {
		t.Error("the hit expired early; a revoked key's window is what that TTL is for")
	}
}

func TestAMissIsCachedLongEnoughToSpareThePostgresLookup(t *testing.T) {
	at := time.Unix(0, 0)
	c := newCache[Credential](30*time.Second, 10)
	c.now = func() time.Time { return at }
	c.store("nvk_live_missing", Credential{Found: false})

	at = at.Add(missTTL / 2)

	if _, cached := c.lookup("nvk_live_missing"); !cached {
		t.Error("a repeated bad key reaches the database every time")
	}
}

// A deployment configured below the miss TTL must not accidentally raise it.
func TestAShortConfiguredTTLIsNotLengthenedForMisses(t *testing.T) {
	at := time.Unix(0, 0)
	c := newCache[Credential](time.Second, 10)
	c.now = func() time.Time { return at }
	c.store("nvk_live_missing", Credential{Found: false})

	at = at.Add(1500 * time.Millisecond)

	if _, cached := c.lookup("nvk_live_missing"); cached {
		t.Error("the miss outlived the configured TTL")
	}
}
