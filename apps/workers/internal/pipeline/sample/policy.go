// Package sample implements tail sampling: it holds a trace's spans until the
// whole trace has been seen, then keeps or drops all of them together.
//
// It sits *after* metric derivation on purpose. Counters are computed from
// every trace, so dropping 90% of healthy traces from storage leaves every
// count, rate and total exact (ADR 0010).
package sample

import (
	"encoding/json"
	"fmt"
	"time"
)

// Policy is one project's sampling rules.
//
// Errors and slow traces are kept whole because they are the traces someone
// opens; healthy traces are kept at a rate, because their value is in the
// aggregate and the aggregate is already exact upstream.
type Policy struct {
	KeepErrors     bool
	SlowThreshold  time.Duration
	HealthyPercent int
}

// DefaultPolicy is ADR 0010's default: every error, every trace slower than the
// threshold, one healthy trace in ten.
func DefaultPolicy(slowThreshold time.Duration, healthyPercent int) Policy {
	return Policy{
		KeepErrors:     true,
		SlowThreshold:  slowThreshold,
		HealthyPercent: healthyPercent,
	}
}

// projectConfig is the JSON shape of projects.sampling_config. Every field is a
// pointer so an absent field keeps the deployment default rather than resetting
// it to a zero that means "drop everything".
type projectConfig struct {
	KeepErrors      *bool `json:"keep_errors"`
	SlowThresholdMS *int  `json:"slow_threshold_ms"`
	HealthyPercent  *int  `json:"healthy_percent"`
}

// PolicyFromJSON layers a project's sampling_config over the deployment
// default. An out-of-range percentage is an error: silently clamping it would
// mean a project believing it stores everything while it stores a tenth.
func PolicyFromJSON(document []byte, fallback Policy) (Policy, error) {
	policy := fallback
	if len(document) == 0 {
		return policy, nil
	}

	var parsed projectConfig
	if err := json.Unmarshal(document, &parsed); err != nil {
		return Policy{}, fmt.Errorf("sampling_config: %w", err)
	}
	if parsed.KeepErrors != nil {
		policy.KeepErrors = *parsed.KeepErrors
	}
	if parsed.SlowThresholdMS != nil {
		if *parsed.SlowThresholdMS < 0 {
			return Policy{}, fmt.Errorf("sampling_config: slow_threshold_ms is %d, want zero or more",
				*parsed.SlowThresholdMS)
		}
		policy.SlowThreshold = time.Duration(*parsed.SlowThresholdMS) * time.Millisecond
	}
	if parsed.HealthyPercent != nil {
		if *parsed.HealthyPercent < 0 || *parsed.HealthyPercent > 100 {
			return Policy{}, fmt.Errorf("sampling_config: healthy_percent is %d, want 0 to 100",
				*parsed.HealthyPercent)
		}
		policy.HealthyPercent = *parsed.HealthyPercent
	}
	return policy, nil
}

// keeps decides one assembled trace.
func (p Policy) keeps(trace traceFacts) bool {
	if p.KeepErrors && trace.errored {
		return true
	}
	if p.SlowThreshold > 0 && trace.duration() >= p.SlowThreshold {
		return true
	}
	return keepsHealthy(trace, p.HealthyPercent)
}

// keepsHealthy decides a healthy trace against its own randomness rather than
// by rolling a die. The verdict is then a property of the trace: reproducible
// in a test, identical on every worker that ever sees it — which is what makes
// a late span landing on a restarted worker agree with the decision already
// taken — and shared with any other sampler using the same rule.
func keepsHealthy(trace traceFacts, percent int) bool {
	return traceRandomness(trace) >= rejectionThreshold(percent)
}

// PolicySource supplies the policy for a project.
type PolicySource interface {
	SamplingPolicy(projectID string) Policy
}

// StaticPolicy is a PolicySource answering with one policy for every project,
// which is what a worker runs on with no control-plane connection configured.
type StaticPolicy Policy

// SamplingPolicy implements PolicySource.
func (s StaticPolicy) SamplingPolicy(string) Policy { return Policy(s) }
