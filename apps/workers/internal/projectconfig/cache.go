// Package projectconfig holds the per-project settings the pipeline stages
// read: which values are masked, and how much of a project's traffic is stored.
//
// The stages cannot query Postgres per span, so the settings are loaded whole,
// cached, and replaced on a timer. Replacement is what makes a rule change take
// effect without restarting a worker.
package projectconfig

import (
	"sync"
	"sync/atomic"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// Settings are one project's pipeline configuration.
type Settings struct {
	Masking  mask.Rules
	Sampling sample.Policy
	// Retention this project's rows are stamped with. Zero means the refresher
	// has not seen the project yet, and the deployment defaults apply.
	RawRetentionDays     uint16
	DerivedRetentionDays uint16

	// FirstTracesStored is how many traces this project's first-traces carve-out
	// has already kept, as the control plane last reported it. Zero — which is
	// what a project no refresh has seen yet gets — leaves the carve-out fully
	// open, and a project that has just been created is exactly that project.
	FirstTracesStored int64
}

// Cache serves settings to the stages. Reads are lock-free: a refresh swaps a
// whole immutable snapshot rather than mutating the map the hot path is reading.
type Cache struct {
	defaults Settings
	snapshot atomic.Pointer[map[string]Settings]

	// storedSinceFlush is what this process has added to a project's
	// first-traces count and not yet written. It is counted into the decision as
	// well as flushed: without it a worker would go on keeping every trace for a
	// whole refresh interval after the carve-out was already full.
	//
	// unflushedProjects mirrors its size, so the steady state — every project
	// long past its carve-out — costs an atomic load rather than the lock.
	unflushed         sync.Mutex
	storedSinceFlush  map[string]int64
	unflushedProjects atomic.Int64
}

// NewCache returns a cache holding nothing but the deployment defaults, which
// is what every project gets until the first refresh succeeds.
func NewCache(defaults Settings) *Cache {
	cache := &Cache{defaults: defaults, storedSinceFlush: map[string]int64{}}
	cache.Replace(nil)
	return cache
}

// Replace installs a new snapshot. A project absent from it falls back to the
// defaults, so removing a project's row restores default behaviour rather than
// leaving its old rules in force.
func (c *Cache) Replace(projects map[string]Settings) {
	if projects == nil {
		projects = map[string]Settings{}
	}
	c.snapshot.Store(&projects)
}

// Loaded returns a project's configuration and whether one was ever loaded for
// it, which Settings cannot express because it answers with the defaults.
func (c *Cache) Loaded(projectID string) (Settings, bool) {
	projects := c.snapshot.Load()
	if projects == nil {
		return c.defaults, false
	}
	configured, ok := (*projects)[projectID]
	if !ok {
		return c.defaults, false
	}
	return configured, true
}

// Settings returns one project's configuration, or the defaults.
func (c *Cache) Settings(projectID string) Settings {
	projects := c.snapshot.Load()
	if projects == nil {
		return c.defaults
	}
	if configured, ok := (*projects)[projectID]; ok {
		return configured
	}
	return c.defaults
}

// MaskingRules implements mask.RulesSource.
func (c *Cache) MaskingRules(projectID string) mask.Rules {
	return c.Settings(projectID).Masking
}

// SamplingPolicy implements sample.PolicySource. firstTraces is the size of the
// carve-out the deployment asked for; the count it is measured against is the
// control plane's, plus whatever this process has stored since its last flush.
func (c *Cache) SamplingPolicy(projectID string, firstTraces int) sample.Policy {
	settings := c.Settings(projectID)
	policy := settings.Sampling
	policy.KeepingFirstTraces = firstTraces > 0 &&
		c.firstTracesStored(projectID, settings.FirstTracesStored) < int64(firstTraces)
	return policy
}

// firstTracesStored is what the control plane reported plus what has not
// reached it yet. Reading the unflushed half is what keeps one worker from
// storing a carve-out's worth of traces again on every refresh interval.
func (c *Cache) firstTracesStored(projectID string, reported int64) int64 {
	if c.unflushedProjects.Load() == 0 {
		return reported
	}
	c.unflushed.Lock()
	defer c.unflushed.Unlock()
	return reported + c.storedSinceFlush[projectID]
}

// RecordStoredTrace implements sample.PolicySource.
func (c *Cache) RecordStoredTrace(projectID string) {
	c.unflushed.Lock()
	defer c.unflushed.Unlock()
	if _, counted := c.storedSinceFlush[projectID]; !counted {
		c.unflushedProjects.Add(1)
	}
	c.storedSinceFlush[projectID]++
}

// takeStoredTraces removes what has been stored since the last flush, for the
// refresher to write. Whatever it cannot write must be handed back.
func (c *Cache) takeStoredTraces() map[string]int64 {
	c.unflushed.Lock()
	defer c.unflushed.Unlock()
	if len(c.storedSinceFlush) == 0 {
		return nil
	}
	taken := c.storedSinceFlush
	c.storedSinceFlush = make(map[string]int64, len(taken))
	c.unflushedProjects.Store(0)
	return taken
}

// returnStoredTraces puts back counts a failed flush could not write, adding to
// whatever arrived in the meantime. Losing them instead would only shorten a
// carve-out that has not been paid for yet, so this errs towards counting twice
// being impossible rather than towards never undercounting.
func (c *Cache) returnStoredTraces(counts map[string]int64) {
	c.unflushed.Lock()
	defer c.unflushed.Unlock()
	for projectID, stored := range counts {
		if _, counted := c.storedSinceFlush[projectID]; !counted {
			c.unflushedProjects.Add(1)
		}
		c.storedSinceFlush[projectID] += stored
	}
}

// Retention is the days this project's raw signals and derived rollups are
// kept for. Falls back to the deployment defaults for a project the refresher
// has not seen, exactly as masking and sampling do.
func (c *Cache) Retention(projectID string) (raw, derived uint16) {
	settings := c.Settings(projectID)
	return settings.RawRetentionDays, settings.DerivedRetentionDays
}

// Configured reports how many projects the current snapshot names, which is
// what a refresh logs.
func (c *Cache) Configured() int {
	projects := c.snapshot.Load()
	if projects == nil {
		return 0
	}
	return len(*projects)
}
