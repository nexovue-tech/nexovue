// Package projectconfig holds the per-project settings the pipeline stages
// read: which values are masked, and how much of a project's traffic is stored.
//
// The stages cannot query Postgres per span, so the settings are loaded whole,
// cached, and replaced on a timer. Replacement is what makes a rule change take
// effect without restarting a worker.
package projectconfig

import (
	"sync/atomic"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// Settings are one project's pipeline configuration.
type Settings struct {
	Masking  mask.Rules
	Sampling sample.Policy
}

// Cache serves settings to the stages. Reads are lock-free: a refresh swaps a
// whole immutable snapshot rather than mutating the map the hot path is reading.
type Cache struct {
	defaults Settings
	snapshot atomic.Pointer[map[string]Settings]
}

// NewCache returns a cache holding nothing but the deployment defaults, which
// is what every project gets until the first refresh succeeds.
func NewCache(defaults Settings) *Cache {
	cache := &Cache{defaults: defaults}
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

// SamplingPolicy implements sample.PolicySource.
func (c *Cache) SamplingPolicy(projectID string) sample.Policy {
	return c.Settings(projectID).Sampling
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
