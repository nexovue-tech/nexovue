// Package health answers the two different questions an orchestrator asks.
//
// Liveness (/healthz) asks "is this process still working?" and touches nothing
// external, because a shared dependency having a bad second must not restart
// every service at once. Readiness (/readyz) asks "can this instance serve
// traffic right now?" and probes each dependency the service actually uses,
// each under its own deadline, so one slow dependency cannot hang the answer.
package health

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
)

// DefaultProbeTimeout bounds a dependency check that does not ask for its own
// deadline. Kept short: readiness must answer well inside a kubelet's timeout.
const DefaultProbeTimeout = 2 * time.Second

// Status is the outcome of a check or of the whole report.
type Status string

// The only two outcomes. Anything degraded but serving is still ok — readiness
// is a traffic decision, not a quality score.
const (
	StatusOK     Status = "ok"
	StatusFailed Status = "failed"
)

// Probe reports whether one dependency is usable right now.
type Probe func(context.Context) error

// Check is one named dependency probe and the deadline it must answer within.
type Check struct {
	Name    string
	Timeout time.Duration
	Probe   Probe
}

// Result is what one check reported.
type Result struct {
	Status     Status `json:"status"`
	DurationMS int64  `json:"duration_ms"`
	Error      string `json:"error,omitempty"`
}

// Report is the body both endpoints return.
type Report struct {
	Status Status            `json:"status"`
	Build  buildinfo.Info    `json:"build"`
	Checks map[string]Result `json:"checks,omitempty"`
}

// Registry holds the dependency checks readiness runs.
type Registry struct {
	mu     sync.RWMutex
	checks []Check
}

// NewRegistry returns an empty registry.
func NewRegistry() *Registry { return &Registry{} }

// Register adds a dependency check. A check with no timeout gets the default.
func (r *Registry) Register(check Check) {
	if check.Timeout <= 0 {
		check.Timeout = DefaultProbeTimeout
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.checks = append(r.checks, check)
}

// Run probes every registered dependency in parallel, each under its own
// timeout, and reports failed if any one of them failed.
func (r *Registry) Run(ctx context.Context) map[string]Result {
	r.mu.RLock()
	checks := make([]Check, len(r.checks))
	copy(checks, r.checks)
	r.mu.RUnlock()

	results := make(map[string]Result, len(checks))
	var mu sync.Mutex
	var wg sync.WaitGroup

	for _, check := range checks {
		wg.Add(1)
		go func(check Check) {
			defer wg.Done()
			result := runCheck(ctx, check)
			mu.Lock()
			results[check.Name] = result
			mu.Unlock()
		}(check)
	}
	wg.Wait()
	return results
}

// runCheck enforces the deadline itself rather than trusting the probe to honour
// its context. Some clients — franz-go's Ping among them — keep retrying past a
// cancelled context, and one such dependency must not hang the whole endpoint.
// The abandoned goroutine ends when the client finally gives up.
func runCheck(ctx context.Context, check Check) Result {
	probeCtx, cancel := context.WithTimeout(ctx, check.Timeout)
	defer cancel()

	started := time.Now()
	failures := make(chan error, 1)
	go func() { failures <- check.Probe(probeCtx) }()

	var err error
	select {
	case err = <-failures:
	case <-probeCtx.Done():
		err = fmt.Errorf("did not answer within %s: %w", check.Timeout, probeCtx.Err())
	}

	result := Result{Status: StatusOK, DurationMS: time.Since(started).Milliseconds()}
	if err != nil {
		result.Status = StatusFailed
		// Probe errors carry DSNs often enough that this cannot be optional:
		// the readiness body is reachable by anything that can reach the port.
		result.Error = logging.RedactCredentials(err.Error())
	}
	return result
}

func overallStatus(results map[string]Result) Status {
	for _, result := range results {
		if result.Status != StatusOK {
			return StatusFailed
		}
	}
	return StatusOK
}
