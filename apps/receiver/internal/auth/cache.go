package auth

import (
	"sync"
	"time"
)

// Credential is one api_keys row as the hot path needs it. A row that does not
// exist is still a credential, with Found false, so misses cache like hits.
type Credential struct {
	Identity Identity

	// KeyHash is the lower-case SHA-256 hex digest of the whole key, as the
	// control plane stored it. Build one with HashKey.
	KeyHash string

	Found bool
}

func (c Credential) found() bool { return c.Found }

// SiteCredential is one site_keys row as the browser door needs it.
//
// It has no hash field, and that is the whole difference: a site key is stored
// in the clear because it ships inside the page that carries it, so the lookup
// matching *is* the verification. There is nothing left to compare, and no
// constant-time comparison to get wrong. See ADR 0086.
type SiteCredential struct {
	Identity Identity

	Found bool
}

func (c SiteCredential) found() bool { return c.Found }

// cacheable is what the shared cache needs of a value: whether the row it
// describes exists, because hits and misses are held for different lengths.
type cacheable interface {
	found() bool
}

// missTTL is how long "no such key" is believed. Long enough that a repeated
// bad key does not reach Postgres on every request, short enough that a key
// created a moment ago starts working almost at once.
const missTTL = 2 * time.Second

type cacheEntry[V cacheable] struct {
	credential V
	expiresAt  time.Time
}

// cache holds credentials for a short TTL, keyed by whatever the door looks a
// key up by — the prefix for a secret key, the whole value for a site key.
//
// Generic over the credential so the two doors share one bounded, expiring map
// rather than each growing its own. There is one answer to "how stale may our
// view of a key be", and one place that answer is implemented.
//
// It is bounded because misses are cached: the prefix of an invalid key is
// attacker-chosen, so an unbounded map is an unbounded memory cost handed to
// whoever can reach the port.
//
// Hits and misses do not live the same length of time, because they are held
// for different reasons. A hit is held to bound how long a *revoked* key keeps
// working. A miss is held only so an invalid key cannot hammer Postgres, and a
// second of that is as good as thirty.
//
// Keeping them equal broke onboarding. A key seconds old is exactly where a
// miss is likely and temporary — the customer pastes it and exports before the
// row is visible — and caching that for the full TTL refused a valid key for
// thirty seconds with nothing to explain it. Measured: five refusals across two
// minutes of the e2e suite, all on keys that existed.
type cache[V cacheable] struct {
	mu         sync.RWMutex
	entries    map[string]cacheEntry[V]
	ttl        time.Duration
	maxEntries int
	now        func() time.Time // replaced in tests; time.Now in production
}

func newCache[V cacheable](ttl time.Duration, maxEntries int) *cache[V] {
	return &cache[V]{
		entries:    make(map[string]cacheEntry[V]),
		ttl:        ttl,
		maxEntries: maxEntries,
		now:        time.Now,
	}
}

func (c *cache[V]) lookup(lookupKey string) (V, bool) {
	c.mu.RLock()
	entry, ok := c.entries[lookupKey]
	c.mu.RUnlock()

	if !ok || !c.now().Before(entry.expiresAt) {
		var absent V
		return absent, false
	}
	return entry.credential, true
}

func (c *cache[V]) store(lookupKey string, value V) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if len(c.entries) >= c.maxEntries {
		c.makeRoom()
	}
	c.entries[lookupKey] = cacheEntry[V]{
		credential: value,
		expiresAt:  c.now().Add(c.lifetime(value)),
	}
}

// lifetime is how long an answer is trusted: the configured TTL for a key that
// exists, and missTTL for one that does not.
func (c *cache[V]) lifetime(value V) time.Duration {
	if !value.found() {
		return min(missTTL, c.ttl)
	}
	return c.ttl
}

// makeRoom drops expired entries and, if that was not enough, everything.
// Emptying the cache costs at most one lookup per live key — bounded, rare, and
// far simpler to reason about than an eviction policy an attacker can steer.
func (c *cache[V]) makeRoom() {
	now := c.now()
	for prefix, entry := range c.entries {
		if !now.Before(entry.expiresAt) {
			delete(c.entries, prefix)
		}
	}
	if len(c.entries) >= c.maxEntries {
		clear(c.entries)
	}
}
