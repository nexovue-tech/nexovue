// Package config loads the receiver's settings from the environment and refuses
// to start on anything missing or malformed, naming the variable at fault.
//
// Nothing here carries a default for a secret: a service that invents a
// credential fails later, somewhere confusing, instead of now.
package config

import (
	"errors"
	"fmt"
	"time"

	"github.com/caarlos0/env/v11"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
)

// ServiceName is the value this binary reports in logs and health responses.
const ServiceName = "receiver"

// DefaultHealthAddr is where the admin listener binds unless configured
// otherwise. The self-check reads it too, so the two cannot drift apart.
const DefaultHealthAddr = ":8081"

// Deployment environments. ENV picks one; it is a log field and a guard against
// a development value reaching production.
const (
	EnvDevelopment = "development"
	EnvStaging     = "staging"
	EnvProduction  = "production"
)

// Config is the receiver's complete settings surface. Every field is read from
// the environment exactly once, at boot.
type Config struct {
	Env       string `env:"ENV,required,notEmpty"`
	LogLevel  string `env:"LOG_LEVEL" envDefault:"info"`
	LogFormat string `env:"LOG_FORMAT" envDefault:"json"`

	// The two OTLP transports listen on the ports the protocol reserves for
	// them. Declared here so the port a receiver listens on is configuration,
	// never a literal.
	OTLPHTTPAddr string `env:"RECEIVER_OTLP_HTTP_ADDR" envDefault:":4318"`
	OTLPGRPCAddr string `env:"RECEIVER_OTLP_GRPC_ADDR" envDefault:":4317"`
	HealthAddr   string `env:"RECEIVER_HEALTH_ADDR" envDefault:":8081"`

	// Profiling mounts Go's runtime profiles on the admin listener — the one
	// above, never an OTLP port. Off by default: the endpoints are
	// unauthenticated, a CPU profile is a remote way to make the process work,
	// and the heap and command line are a remote way to read it. Turning it on
	// is a statement that the admin port is not published, which is what
	// `health.WithProfiling` records having checked.
	Profiling bool `env:"RECEIVER_PPROF_ENABLED" envDefault:"false"`

	KafkaBrokers []string `env:"KAFKA_BROKERS,required,notEmpty" envSeparator:","`
	PostgresDSN  string   `env:"POSTGRES_DSN,required,notEmpty"`

	// Where each signal's envelopes are published. Configuration rather than
	// literals, so a deployment can rename a topic without a build.
	SpansTopic    string `env:"KAFKA_SPANS_TOPIC" envDefault:"spans-raw"`
	LogsTopic     string `env:"KAFKA_LOGS_TOPIC" envDefault:"logs-raw"`
	MetricsTopic  string `env:"KAFKA_METRICS_TOPIC" envDefault:"metrics-raw"`
	ProfilesTopic string `env:"KAFKA_PROFILES_TOPIC" envDefault:"profiles-raw"`

	// KafkaMaxBufferedBytes caps what the producer holds while the broker is
	// behind. franz-go buffers without limit by default, so an outage would grow
	// the process until it dies instead of refusing work the sender can retry.
	// Eight times the largest legal batch: enough concurrent exports to keep
	// throughput, small enough to stay a bound.
	KafkaMaxBufferedBytes int64 `env:"RECEIVER_KAFKA_MAX_BUFFERED_BYTES" envDefault:"134217728"`

	// ProduceTimeout bounds one publish: how long the receiver will spend
	// handing a batch to Kafka before answering "retry". It is both the client's
	// delivery deadline and the publisher's own budget, so neither outlives the
	// other and the answer never takes longer than a sender will wait.
	ProduceTimeout time.Duration `env:"RECEIVER_PRODUCE_TIMEOUT" envDefault:"5s"`

	// IngestSummaryInterval is how often the door writes its traffic summary.
	// That line is the only proof the receiver is serving: without it a silently
	// broken receiver and a healthy one produce identical logs.
	IngestSummaryInterval time.Duration `env:"RECEIVER_INGEST_SUMMARY_INTERVAL" envDefault:"60s"`

	// MaxBodyBytes caps the compressed request as it arrives; MaxDecodedBodyBytes
	// caps what a gzip body may expand to, which is the limit that stops a bomb.
	MaxBodyBytes        int64 `env:"RECEIVER_MAX_BODY_BYTES" envDefault:"4194304"`
	MaxDecodedBodyBytes int64 `env:"RECEIVER_MAX_DECODED_BODY_BYTES" envDefault:"16777216"`

	// MaxInflightExports bounds how many export bodies this process holds at
	// once, across both doors and both transports; MaxUnverifiedExports is the
	// smaller share for gRPC exports whose key the process has never seen. A
	// full-size export measured ≈ 46 MiB held, so the defaults keep the worst
	// case under the development receiver's 768 MiB — sized from the laptop
	// until a load test on the hosting target replaces them.
	MaxInflightExports   int `env:"RECEIVER_MAX_INFLIGHT_EXPORTS" envDefault:"8"`
	MaxUnverifiedExports int `env:"RECEIVER_MAX_UNVERIFIED_EXPORTS" envDefault:"2"`

	// APIKeyCacheTTL bounds how long a revoked key keeps working, so it is
	// validated against a ceiling rather than merely required to be positive.
	APIKeyCacheTTL        time.Duration `env:"RECEIVER_API_KEY_CACHE_TTL" envDefault:"30s"`
	APIKeyCacheMaxEntries int           `env:"RECEIVER_API_KEY_CACHE_MAX_ENTRIES" envDefault:"10000"`

	// AllowanceCacheTTL bounds how far past its included volume an organization
	// can get before the door notices, and how long an upgrade takes to open the
	// door again. Its own setting rather than the key cache's: that one is sized
	// for how long a revoked key may keep working, and moving it for a security
	// reason must not silently change what a customer is billed.
	// One entry per organization this receiver has seen, so the ceiling is a
	// bound on tenants rather than on attacker-chosen strings.
	AllowanceCacheTTL        time.Duration `env:"RECEIVER_ALLOWANCE_CACHE_TTL" envDefault:"30s"`
	AllowanceCacheMaxEntries int           `env:"RECEIVER_ALLOWANCE_CACHE_MAX_ENTRIES" envDefault:"10000"`

	RateLimitPerSecond int `env:"RECEIVER_RATE_LIMIT_PER_SECOND" envDefault:"1000"`

	// How many receivers share the traffic. Each counts in its own memory and
	// enforces its share — every per-second limit divided by this — so a fleet of
	// three says 3, or each would allow three times the policy (ADR-020).
	Instances int `env:"RECEIVER_INSTANCES" envDefault:"1"`

	// ProfileSamplesPerSecond bounds one key's profile samples a second, because
	// profiles spend no allowance while in preview (F17, D1). The eBPF profiler
	// samples each busy core about 19 times a second, so a saturated 64-core host
	// sends ~1,200: this is a fleet of such hosts per key, and a flood — measured
	// at 400,000 a second from one key — is refused instead of stalling every
	// tenant's profiles.
	ProfileSamplesPerSecond int `env:"RECEIVER_PROFILE_SAMPLES_PER_SECOND" envDefault:"20000"`

	// Pool sizes are stated rather than inherited: pgx defaults to one
	// connection per CPU, which is far more than a cached key lookup needs and
	// scales with the host rather than with the workload.
	PostgresMaxConns int32 `env:"RECEIVER_POSTGRES_MAX_CONNS" envDefault:"10"`
	PostgresMinConns int32 `env:"RECEIVER_POSTGRES_MIN_CONNS" envDefault:"2"`

	ShutdownTimeout time.Duration `env:"SHUTDOWN_TIMEOUT" envDefault:"20s"`
}

// EnvelopeOverheadBytes is the headroom a wrapped payload needs over the body it
// came from: tenancy, ids and the semconv version travel beside it. franz-go
// fails a record larger than its buffer outright and no retry can fix that, so
// the buffer has to clear the largest envelope a legal body can produce.
const EnvelopeOverheadBytes = 4096

// MaxRecordBytes is the largest record a legal request can produce, and so the
// size every stage after the door must carry: the producer's batch cap here,
// and max.message.bytes on the broker (deploy/kafka/topics.sh). A stage set
// below this refuses a trace nothing can make smaller, which the sender then
// retries until it gives up — data loss dressed as backpressure.
func (c Config) MaxRecordBytes() int64 {
	return c.MaxDecodedBodyBytes + EnvelopeOverheadBytes
}

// MaxProducerBatchBytes is franz-go's own ceiling on a record batch. Validation
// holds MaxRecordBytes under it, which is what makes the int32 narrowing below
// safe rather than merely likely.
const MaxProducerBatchBytes int64 = 1 << 30

// ProducerBatchMaxBytes is MaxRecordBytes in the width franz-go's option takes.
func (c Config) ProducerBatchMaxBytes() int32 {
	if c.MaxRecordBytes() > MaxProducerBatchBytes {
		return int32(MaxProducerBatchBytes)
	}
	return int32(c.MaxRecordBytes()) //nolint:gosec // G115: validate() holds this under MaxProducerBatchBytes
}

// MaxAPIKeyCacheTTL is the ceiling on RECEIVER_API_KEY_CACHE_TTL. Revoking a key
// is a security action, so the window in which a withdrawn key still works is
// capped by the code rather than left to whoever writes the deployment.
const MaxAPIKeyCacheTTL = time.Minute

// MaxAllowanceCacheTTL is the ceiling on RECEIVER_ALLOWANCE_CACHE_TTL. It is
// how long a customer who has just paid to raise their allowance keeps being
// refused, so it is capped by the code rather than left to a deployment.
const MaxAllowanceCacheTTL = 5 * time.Minute

// Load reads and validates the environment, returning an error that names the
// offending variable.
func Load() (Config, error) {
	var cfg Config
	if err := env.Parse(&cfg); err != nil {
		return Config{}, fmt.Errorf("invalid configuration: %w", err)
	}
	if err := cfg.validate(); err != nil {
		return Config{}, fmt.Errorf("invalid configuration: %w", err)
	}
	return cfg, nil
}

func (c Config) validate() error {
	return errors.Join(
		oneOf("ENV", c.Env, EnvDevelopment, EnvStaging, EnvProduction),
		oneOf("LOG_LEVEL", c.LogLevel, logging.Levels()...),
		oneOf("LOG_FORMAT", c.LogFormat, logging.Formats()...),
		listeningAddress("RECEIVER_OTLP_HTTP_ADDR", c.OTLPHTTPAddr),
		listeningAddress("RECEIVER_OTLP_GRPC_ADDR", c.OTLPGRPCAddr),
		listeningAddress("RECEIVER_HEALTH_ADDR", c.HealthAddr),
		noBlankEntries("KAFKA_BROKERS", c.KafkaBrokers),
		connectionURL("POSTGRES_DSN", c.PostgresDSN, "postgres", "postgresql"),
		notBlank("KAFKA_SPANS_TOPIC", c.SpansTopic),
		notBlank("KAFKA_LOGS_TOPIC", c.LogsTopic),
		notBlank("KAFKA_METRICS_TOPIC", c.MetricsTopic),
		notBlank("KAFKA_PROFILES_TOPIC", c.ProfilesTopic),
		positiveSize("RECEIVER_MAX_BODY_BYTES", c.MaxBodyBytes),
		positiveSize("RECEIVER_MAX_DECODED_BODY_BYTES", c.MaxDecodedBodyBytes),
		atLeastSize("RECEIVER_MAX_DECODED_BODY_BYTES", c.MaxDecodedBodyBytes,
			"RECEIVER_MAX_BODY_BYTES", c.MaxBodyBytes),
		positiveSize("RECEIVER_KAFKA_MAX_BUFFERED_BYTES", c.KafkaMaxBufferedBytes),
		atLeastSize("RECEIVER_KAFKA_MAX_BUFFERED_BYTES", c.KafkaMaxBufferedBytes,
			"RECEIVER_MAX_DECODED_BODY_BYTES plus envelope overhead",
			c.MaxRecordBytes()),
		atMostSize("RECEIVER_MAX_DECODED_BODY_BYTES plus envelope overhead",
			c.MaxRecordBytes(), "the largest batch franz-go accepts", MaxProducerBatchBytes),
		positiveCount("RECEIVER_MAX_INFLIGHT_EXPORTS", c.MaxInflightExports),
		positiveCount("RECEIVER_MAX_UNVERIFIED_EXPORTS", c.MaxUnverifiedExports),
		positiveDuration("RECEIVER_PRODUCE_TIMEOUT", c.ProduceTimeout),
		positiveDuration("RECEIVER_INGEST_SUMMARY_INTERVAL", c.IngestSummaryInterval),
		positiveDuration("RECEIVER_API_KEY_CACHE_TTL", c.APIKeyCacheTTL),
		atMostDuration("RECEIVER_API_KEY_CACHE_TTL", c.APIKeyCacheTTL, MaxAPIKeyCacheTTL),
		positiveCount("RECEIVER_API_KEY_CACHE_MAX_ENTRIES", c.APIKeyCacheMaxEntries),
		positiveDuration("RECEIVER_ALLOWANCE_CACHE_TTL", c.AllowanceCacheTTL),
		atMostDuration("RECEIVER_ALLOWANCE_CACHE_TTL", c.AllowanceCacheTTL, MaxAllowanceCacheTTL),
		positiveCount("RECEIVER_ALLOWANCE_CACHE_MAX_ENTRIES", c.AllowanceCacheMaxEntries),
		positiveCount("RECEIVER_RATE_LIMIT_PER_SECOND", c.RateLimitPerSecond),
		positiveCount("RECEIVER_INSTANCES", c.Instances),
		positiveCount("RECEIVER_PROFILE_SAMPLES_PER_SECOND", c.ProfileSamplesPerSecond),
		positiveCount("RECEIVER_POSTGRES_MAX_CONNS", int(c.PostgresMaxConns)),
		positiveCount("RECEIVER_POSTGRES_MIN_CONNS", int(c.PostgresMinConns)),
		atMostCount("RECEIVER_POSTGRES_MIN_CONNS", int(c.PostgresMinConns),
			"RECEIVER_POSTGRES_MAX_CONNS", int(c.PostgresMaxConns)),
		positiveDuration("SHUTDOWN_TIMEOUT", c.ShutdownTimeout),
	)
}
