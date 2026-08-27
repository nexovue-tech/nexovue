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

	KafkaBrokers []string `env:"KAFKA_BROKERS,required,notEmpty" envSeparator:","`
	PostgresDSN  string   `env:"POSTGRES_DSN,required,notEmpty"`
	RedisURL     string   `env:"REDIS_URL,required,notEmpty"`

	// SpansTopic is where split trace envelopes are published. Configuration
	// rather than a literal, so a deployment can rename a topic without a build.
	SpansTopic string `env:"KAFKA_SPANS_TOPIC" envDefault:"spans-raw"`

	// MaxBodyBytes caps the compressed request as it arrives; MaxDecodedBodyBytes
	// caps what a gzip body may expand to, which is the limit that stops a bomb.
	MaxBodyBytes        int64 `env:"RECEIVER_MAX_BODY_BYTES" envDefault:"4194304"`
	MaxDecodedBodyBytes int64 `env:"RECEIVER_MAX_DECODED_BODY_BYTES" envDefault:"16777216"`

	// APIKeyCacheTTL bounds how long a revoked key keeps working, so it is
	// validated against a ceiling rather than merely required to be positive.
	APIKeyCacheTTL        time.Duration `env:"RECEIVER_API_KEY_CACHE_TTL" envDefault:"30s"`
	APIKeyCacheMaxEntries int           `env:"RECEIVER_API_KEY_CACHE_MAX_ENTRIES" envDefault:"10000"`

	RateLimitPerSecond int `env:"RECEIVER_RATE_LIMIT_PER_SECOND" envDefault:"1000"`

	// Pool sizes are stated rather than inherited: pgx defaults to one
	// connection per CPU, which is far more than a cached key lookup needs and
	// scales with the host rather than with the workload (ADR 0055).
	PostgresMaxConns int32 `env:"RECEIVER_POSTGRES_MAX_CONNS" envDefault:"10"`
	PostgresMinConns int32 `env:"RECEIVER_POSTGRES_MIN_CONNS" envDefault:"2"`

	ShutdownTimeout time.Duration `env:"SHUTDOWN_TIMEOUT" envDefault:"20s"`
}

// MaxAPIKeyCacheTTL is the ceiling on RECEIVER_API_KEY_CACHE_TTL. Revoking a key
// is a security action, so the window in which a withdrawn key still works is
// capped by the code rather than left to whoever writes the deployment.
const MaxAPIKeyCacheTTL = time.Minute

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
		connectionURL("REDIS_URL", c.RedisURL, "redis", "rediss"),
		notBlank("KAFKA_SPANS_TOPIC", c.SpansTopic),
		positiveSize("RECEIVER_MAX_BODY_BYTES", c.MaxBodyBytes),
		positiveSize("RECEIVER_MAX_DECODED_BODY_BYTES", c.MaxDecodedBodyBytes),
		atLeastSize("RECEIVER_MAX_DECODED_BODY_BYTES", c.MaxDecodedBodyBytes,
			"RECEIVER_MAX_BODY_BYTES", c.MaxBodyBytes),
		positiveDuration("RECEIVER_API_KEY_CACHE_TTL", c.APIKeyCacheTTL),
		atMostDuration("RECEIVER_API_KEY_CACHE_TTL", c.APIKeyCacheTTL, MaxAPIKeyCacheTTL),
		positiveCount("RECEIVER_API_KEY_CACHE_MAX_ENTRIES", c.APIKeyCacheMaxEntries),
		positiveCount("RECEIVER_RATE_LIMIT_PER_SECOND", c.RateLimitPerSecond),
		positiveCount("RECEIVER_POSTGRES_MAX_CONNS", int(c.PostgresMaxConns)),
		positiveCount("RECEIVER_POSTGRES_MIN_CONNS", int(c.PostgresMinConns)),
		atMostCount("RECEIVER_POSTGRES_MIN_CONNS", int(c.PostgresMinConns),
			"RECEIVER_POSTGRES_MAX_CONNS", int(c.PostgresMaxConns)),
		positiveDuration("SHUTDOWN_TIMEOUT", c.ShutdownTimeout),
	)
}
