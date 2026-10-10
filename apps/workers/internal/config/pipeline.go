package config

import (
	"errors"
	"time"
)

// Masking is the PII masking stage's settings.
//
// There is no off switch. The default rules only match values that can never be
// legitimately needed for debugging, and a variable that turns masking off is a
// variable that eventually gets set in production by accident.
type Masking struct {
	// ProjectRefresh is how often per-project rules are reloaded from the
	// control plane, and therefore how long a rule change takes to apply.
	ProjectRefresh time.Duration `env:"WORKERS_PROJECT_CONFIG_REFRESH" envDefault:"30s"`

	// HashSecret keys the pseudonyms a hashing rule produces, base64-encoded.
	//
	// A rule can replace a value with a stable pseudonym instead of blanking it,
	// so "the same user across these requests" stays answerable without saying
	// who. That is only worth anything keyed: an unkeyed digest of an email or an
	// account id is brute-forced from a candidate list, which is why PCI DSS v4
	// stopped accepting plain hashes of card numbers.
	//
	// Absent, and hashing is simply unavailable — a project that asks for it gets
	// a rejected config document rather than a weak pseudonym. Masking itself
	// still runs; the default rules blank, and blanking needs no secret. A worker
	// that refused to boot without this would let one missing variable stop
	// ingest for every tenant.
	//
	// Every replica must share the same value. Two workers with different secrets
	// produce two pseudonyms for one person, silently.
	HashSecret string `env:"WORKERS_MASKING_HASH_SECRET"`
}

func (m Masking) validate() error {
	return errors.Join(
		positiveDuration("WORKERS_PROJECT_CONFIG_REFRESH", m.ProjectRefresh),
		optionalBase64Secret("WORKERS_MASKING_HASH_SECRET", m.HashSecret, maskHashSecretMinBytes),
	)
}

// maskHashSecretMinBytes is the width below which an HMAC key stops carrying its
// nominal strength: SHA-256's block-equivalent security is 256 bits.
const maskHashSecretMinBytes = 32

// Sampling is the tail-sampling stage's settings: the sampling policy plus the
// bounds that keep the buffer from becoming a memory leak with a traffic spike
// in front of it.
type Sampling struct {
	// Window is how long a trace waits for the rest of its spans before it is
	// judged. It is a floor under the span-to-queryable lag, so it is traded
	// against that budget and not chosen for sampling accuracy alone. The first
	// proposal was ~30s; 10s is what the p99 < 15s budget leaves room for.
	Window time.Duration `env:"WORKERS_SAMPLING_WINDOW" envDefault:"10s"`

	// DecisionTTL is how long a decided trace is remembered so a span arriving
	// after the window follows its trace rather than being judged alone.
	DecisionTTL time.Duration `env:"WORKERS_SAMPLING_DECISION_TTL" envDefault:"60s"`

	// SlowThreshold is the fixed duration above which a trace is kept whole.
	// Per-endpoint baselines replace it later; the first version is a fixed
	// number.
	SlowThreshold time.Duration `env:"WORKERS_SAMPLING_SLOW_THRESHOLD" envDefault:"2s"`

	// HealthyPercent is the share of traces that are neither failed nor slow
	// that reach storage. 100 stores everything, which is what a deployment
	// sets while it is still small enough not to care.
	HealthyPercent int `env:"WORKERS_SAMPLING_HEALTHY_PERCENT" envDefault:"10"`

	// FirstTraces is how many traces a project stores whole before
	// HealthyPercent applies to it at all.
	//
	// A customer's first span is a hello-world request that worked, so at 10% it
	// had a nine in ten chance of being dropped and the onboarding screen waited
	// for a trace that was never written. The count is kept per project in the
	// control plane, so it is the project's first traces and not each worker's.
	// Zero switches the carve-out off, which is also what a worker with no
	// control-plane connection runs on: nothing there can count.
	FirstTraces int `env:"WORKERS_SAMPLING_FIRST_TRACES" envDefault:"100"`

	MaxTraces int `env:"WORKERS_SAMPLING_MAX_TRACES" envDefault:"50000"`
	MaxSpans  int `env:"WORKERS_SAMPLING_MAX_SPANS" envDefault:"500000"`
	// MaxBytes bounds what the buffer holds, not just how many: a quarter of the
	// worker's 1 GiB, sized from the laptop until a load test replaces it.
	MaxBytes int `env:"WORKERS_SAMPLING_MAX_BYTES" envDefault:"268435456"`
}

func (s Sampling) validate() error {
	return errors.Join(
		positiveDuration("WORKERS_SAMPLING_WINDOW", s.Window),
		positiveDuration("WORKERS_SAMPLING_DECISION_TTL", s.DecisionTTL),
		positiveDuration("WORKERS_SAMPLING_SLOW_THRESHOLD", s.SlowThreshold),
		percentage("WORKERS_SAMPLING_HEALTHY_PERCENT", s.HealthyPercent),
		nonNegativeCount("WORKERS_SAMPLING_FIRST_TRACES", s.FirstTraces),
		positiveCount("WORKERS_SAMPLING_MAX_TRACES", s.MaxTraces),
		positiveCount("WORKERS_SAMPLING_MAX_SPANS", s.MaxSpans),
		positiveCount("WORKERS_SAMPLING_MAX_BYTES", s.MaxBytes),
	)
}

// Retention is what a row is stamped with when the control plane has not said
// otherwise — a project the refresher has not seen yet, or a deployment with no
// control plane at all. Every row carries its own retention so a whole part can
// expire at once instead of being rewritten row by row.
type Retention struct {
	// Spans and logs.
	RawDays uint16 `env:"WORKERS_RETENTION_RAW_DAYS" envDefault:"30"`
	// Metrics and rollups.
	DerivedDays uint16 `env:"WORKERS_RETENTION_DERIVED_DAYS" envDefault:"395"`
}

// Metrics is the exact-counter stage's settings.
type Metrics struct {
	// LateGrace is how long a minute stays open after it ends. A span arriving
	// later still lands in its own minute; it just costs a second row for it.
	//
	// It also sets consumer lag: a batch's offsets wait for its counters to be
	// written, so commits trail ingest by up to one minute plus this.
	LateGrace time.Duration `env:"WORKERS_METRICS_LATE_GRACE" envDefault:"10s"`
}

func (m Metrics) validate() error {
	return positiveDuration("WORKERS_METRICS_LATE_GRACE", m.LateGrace)
}
