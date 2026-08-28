// Package config loads a worker's settings from the environment and refuses to
// start on anything missing or malformed, naming the variable at fault.
//
// All four worker binaries share one settings surface: they consume the same
// broker and write to the same store, and differ only in which topic they read.
// Nothing here carries a default for a secret.
package config

import (
	"errors"
	"fmt"
	"time"

	"github.com/caarlos0/env/v11"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/logging"
)

// Deployment environments. ENV picks one; it is a log field and a guard against
// a development value reaching production.
const (
	EnvDevelopment = "development"
	EnvStaging     = "staging"
	EnvProduction  = "production"
)

// DefaultHealthAddr is where the admin listener binds unless configured
// otherwise. The self-check reads it too, so the two cannot drift apart.
const DefaultHealthAddr = ":8081"

// Config is a worker's complete settings surface, read from the environment
// exactly once, at boot.
type Config struct {
	Env       string `env:"ENV,required,notEmpty"`
	LogLevel  string `env:"LOG_LEVEL" envDefault:"info"`
	LogFormat string `env:"LOG_FORMAT" envDefault:"json"`

	HealthAddr string `env:"WORKERS_HEALTH_ADDR" envDefault:":8081"`

	KafkaBrokers  []string `env:"KAFKA_BROKERS,required,notEmpty" envSeparator:","`
	ClickHouseDSN string   `env:"CLICKHOUSE_DSN,required,notEmpty"`

	// Sized explicitly rather than left to the client default, which is one
	// connection per CPU and so differs on every machine.
	ClickHouseMaxConns int32 `env:"CLICKHOUSE_MAX_CONNS" envDefault:"4"`

	// ClickHouse is the parsed form of ClickHouseDSN. Parsing at boot is what
	// turns a typo in the DSN into a startup failure instead of a write error
	// an hour into a backlog.
	ClickHouse ClickHouseTarget `env:"-"`

	// Which pipelines this process runs. Defaults to all of them: one container
	// until load justifies more. In production the same image runs several
	// times with different selections.
	PipelinesRaw string `env:"WORKER_PIPELINES" envDefault:"all"`

	// Pipelines is PipelinesRaw parsed, so an unknown name fails at boot.
	Pipelines []Pipeline `env:"-"`

	// Spans configures the trace-assembly pipeline.
	Spans SpanIngest

	// Postgres is the optional control-plane connection the mask and sample
	// stages read per-project settings from.
	Postgres Postgres

	// The stages of the trace-assembly pipeline, in the order they run:
	// decode → mask → derive → sample → sink.
	Masking   Masking
	Metrics   Metrics
	Sampling  Sampling
	Retention Retention

	ShutdownTimeout time.Duration `env:"SHUTDOWN_TIMEOUT" envDefault:"30s"`
}

// Load reads and validates the environment, returning an error that names the
// offending variable.
func Load() (Config, error) {
	var cfg Config
	if err := env.Parse(&cfg); err != nil {
		return Config{}, fmt.Errorf("invalid configuration: %w", err)
	}

	target, err := parseClickHouseDSN(clickHouseDSNVariable, cfg.ClickHouseDSN)
	if err != nil {
		return Config{}, fmt.Errorf("invalid configuration: %w", err)
	}
	cfg.ClickHouse = target

	pipelines, err := ParsePipelines(cfg.PipelinesRaw)
	if err != nil {
		return Config{}, fmt.Errorf("invalid configuration: %w", err)
	}
	cfg.Pipelines = pipelines

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
		listeningAddress("WORKERS_HEALTH_ADDR", c.HealthAddr),
		noBlankEntries("KAFKA_BROKERS", c.KafkaBrokers),
		positiveCount("CLICKHOUSE_MAX_CONNS", int(c.ClickHouseMaxConns)),
		positiveDuration("SHUTDOWN_TIMEOUT", c.ShutdownTimeout),
		c.Spans.validate(),
		c.Postgres.validate(),
		c.Masking.validate(),
		c.Metrics.validate(),
		c.Sampling.validate(),
	)
}
