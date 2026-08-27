package measure

import (
	"sync"
	"time"
)

// sampleClock answers "has interval elapsed since the last yes?" for callers on
// many goroutines. Spacing samples by time rather than by request count keeps
// them spread evenly however fast the run turns out to go.
type sampleClock struct {
	mu   sync.Mutex
	last time.Time
}

func newSampleClock() *sampleClock {
	return &sampleClock{}
}

func (c *sampleClock) due(now time.Time, interval time.Duration) bool {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.last.IsZero() && now.Sub(c.last) < interval {
		return false
	}
	c.last = now
	return true
}
