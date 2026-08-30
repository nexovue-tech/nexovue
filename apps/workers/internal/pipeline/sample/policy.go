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

	// KeepingFirstTraces is true while a project is still storing the first
	// traces it has ever sent, which are kept whatever the percentage says.
	//
	// A customer's first span is a hello-world request that worked — healthy and
	// fast — so at the default 10% it had a nine in ten chance of being dropped,
	// and the onboarding screen, which reads storage, waited for something that
	// was never written.
	//
	// The zero value is the safe one: only a source that can count what a
	// project has stored may set it, because a carve-out nothing can close would
	// store every trace for ever.
	KeepingFirstTraces bool
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
	if p.KeepingFirstTraces {
		return true
	}
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

// PolicySource supplies the policy for a project and counts the traces the
// first-traces carve-out keeps for it.
//
// firstTraces — how many traces that carve-out covers — is asked rather than
// known, because the two halves of the question live apart: the deployment sets
// the size of the carve-out, the control plane knows how far a project has got
// through it, and neither one knows both.
type PolicySource interface {
	SamplingPolicy(projectID string, firstTraces int) Policy

	// RecordStoredTrace says the carve-out kept one more trace for this project.
	// It is what makes the count shared: a project's first traces are its first
	// ones across the fleet, not its first ones in each worker process.
	RecordStoredTrace(projectID string)
}

// StaticPolicy is a PolicySource answering with one policy for every project,
// which is what a worker runs on with no control-plane connection configured.
type StaticPolicy Policy

// SamplingPolicy implements PolicySource. The carve-out stays off whatever the
// deployment asked for: nothing here can count what a project has stored, and a
// carve-out that can never close would store every trace for ever.
func (s StaticPolicy) SamplingPolicy(string, int) Policy {
	policy := Policy(s)
	policy.KeepingFirstTraces = false
	return policy
}

// RecordStoredTrace implements PolicySource. There is nowhere to count.
func (StaticPolicy) RecordStoredTrace(string) {}
