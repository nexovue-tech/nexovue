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

// missTTL is how long "no such key" is believed. Long enough that a repeated
// bad key does not reach Postgres on every request, short enough that a key
// created a moment ago starts working almost at once.
const missTTL = 2 * time.Second

type cacheEntry struct {
	credential Credential
	expiresAt  time.Time
}

// cache holds credentials for a short TTL, keyed by the key prefix.
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
type cache struct {
	mu         sync.RWMutex
	entries    map[string]cacheEntry
	ttl        time.Duration
	maxEntries int
	now        func() time.Time // replaced in tests; time.Now in production
}

func newCache(ttl time.Duration, maxEntries int) *cache {
	return &cache{
		entries:    make(map[string]cacheEntry),
		ttl:        ttl,
		maxEntries: maxEntries,
		now:        time.Now,
	}
}

func (c *cache) lookup(prefix string) (Credential, bool) {
	c.mu.RLock()
	entry, ok := c.entries[prefix]
	c.mu.RUnlock()

	if !ok || !c.now().Before(entry.expiresAt) {
		return Credential{}, false
	}
	return entry.credential, true
}

func (c *cache) store(prefix string, value Credential) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if len(c.entries) >= c.maxEntries {
		c.makeRoom()
	}
	c.entries[prefix] = cacheEntry{credential: value, expiresAt: c.now().Add(c.lifetime(value))}
}

// lifetime is how long an answer is trusted: the configured TTL for a key that
// exists, and missTTL for one that does not.
func (c *cache) lifetime(value Credential) time.Duration {
	if !value.Found {
		return min(missTTL, c.ttl)
	}
	return c.ttl
}

// makeRoom drops expired entries and, if that was not enough, everything.
// Emptying the cache costs at most one lookup per live key — bounded, rare, and
// far simpler to reason about than an eviction policy an attacker can steer.
func (c *cache) makeRoom() {
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
