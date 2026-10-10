// Package cache holds one bounded, expiring map, shared by everything on the
// ingest path that answers from a short-lived copy of a control-plane row.
//
// It exists as its own package because there is one answer to "how stale may
// our view of the control plane be", and a second copy of this map would be a
// second answer nobody chose. The credential doors and the plan allowance both
// use it.
package cache

import (
	"sync"
	"time"
)

// Value is what the cache needs of what it holds: whether the row it describes
// exists, because hits and misses are held for different lengths.
type Value interface {
	// Exists reports whether the control plane had a row to answer with. A
	// miss is still an answer and is still cached; it is just believed for
	// less time.
	Exists() bool
}

// MissTTL is how long "no such row" is believed. Long enough that a repeated
// bad lookup does not reach Postgres on every request, short enough that a row
// created a moment ago starts working almost at once.
const MissTTL = 2 * time.Second

type entry[V Value] struct {
	value     V
	expiresAt time.Time
}

// Cache holds values for a short TTL, keyed by whatever the caller looks them
// up by — the prefix for a secret key, the whole value for a site key, the
// organization id for a plan allowance.
//
// It is bounded because misses are cached: the prefix of an invalid key is
// attacker-chosen, so an unbounded map is an unbounded memory cost handed to
// whoever can reach the port.
//
// Hits and misses do not live the same length of time, because they are held
// for different reasons. A hit is held to bound how long a *withdrawn* row
// keeps being believed. A miss is held only so an invalid lookup cannot hammer
// Postgres, and a second of that is as good as thirty.
//
// Keeping them equal broke onboarding. A key seconds old is exactly where a
// miss is likely and temporary — the customer pastes it and exports before the
// row is visible — and caching that for the full TTL refused a valid key for
// thirty seconds with nothing to explain it. Measured: five refusals across two
// minutes of the e2e suite, all on keys that existed.
type Cache[V Value] struct {
	mu         sync.RWMutex
	entries    map[string]entry[V]
	ttl        time.Duration
	maxEntries int
	now        func() time.Time // replaced in tests; time.Now in production
}

// New returns an empty cache holding at most maxEntries for ttl each.
func New[V Value](ttl time.Duration, maxEntries int) *Cache[V] {
	return &Cache[V]{
		entries:    make(map[string]entry[V]),
		ttl:        ttl,
		maxEntries: maxEntries,
		now:        time.Now,
	}
}

// Lookup returns a value still inside its lifetime, reporting whether there was
// one.
func (c *Cache[V]) Lookup(key string) (V, bool) {
	c.mu.RLock()
	held, ok := c.entries[key]
	// Read under the same lock the clock is replaced under, so a test seam
	// cannot become a data race.
	now := c.now()
	c.mu.RUnlock()

	if !ok || !now.Before(held.expiresAt) {
		var absent V
		return absent, false
	}
	return held.value, true
}

// Peek returns whatever is held for key, expired or not, and whether it is
// still inside its lifetime.
//
// Never an answer to authenticate with — Lookup is that. It is for a pre-check
// that only decides how cautiously to treat a request the full check will still
// judge: a key that was valid a minute ago is very likely still a customer's.
func (c *Cache[V]) Peek(key string) (value V, fresh, held bool) {
	c.mu.RLock()
	stored, ok := c.entries[key]
	now := c.now()
	c.mu.RUnlock()

	if !ok {
		return value, false, false
	}
	return stored.value, now.Before(stored.expiresAt), true
}

// Store keeps a value for as long as its kind of answer is believed.
func (c *Cache[V]) Store(key string, value V) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if len(c.entries) >= c.maxEntries {
		c.makeRoom()
	}
	c.entries[key] = entry[V]{value: value, expiresAt: c.now().Add(c.lifetime(value))}
}

// UseClock replaces this cache's view of time. It exists for tests: expiry is
// the whole behaviour here, and a suite that proved it with real sleeps would
// be both slow and flaky.
func (c *Cache[V]) UseClock(now func() time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = now
}

// Len is how many entries are held, expired ones included. Bounds are what this
// type promises, so there is a way to check one.
func (c *Cache[V]) Len() int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.entries)
}

// lifetime is how long an answer is trusted: the configured TTL for a row that
// exists, and MissTTL for one that does not.
func (c *Cache[V]) lifetime(value V) time.Duration {
	if !value.Exists() {
		return min(MissTTL, c.ttl)
	}
	return c.ttl
}

// makeRoom drops expired entries and, if that was not enough, everything.
// Emptying the cache costs at most one lookup per live key — bounded, rare, and
// far simpler to reason about than an eviction policy an attacker can steer.
func (c *Cache[V]) makeRoom() {
	now := c.now()
	for key, held := range c.entries {
		if !now.Before(held.expiresAt) {
			delete(c.entries, key)
		}
	}
	if len(c.entries) >= c.maxEntries {
		clear(c.entries)
	}
}
