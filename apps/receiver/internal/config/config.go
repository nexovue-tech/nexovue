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

	// OTLPHTTPAddr is bound by the OTLP endpoint in P1-12; it is declared here
	// so the port a receiver listens on is configuration, never a literal.
	OTLPHTTPAddr string `env:"RECEIVER_OTLP_HTTP_ADDR" envDefault:":4318"`
	HealthAddr   string `env:"RECEIVER_HEALTH_ADDR" envDefault:":8081"`

	KafkaBrokers []string `env:"KAFKA_BROKERS,required,notEmpty" envSeparator:","`
	PostgresDSN  string   `env:"POSTGRES_DSN,required,notEmpty"`
	RedisURL     string   `env:"REDIS_URL,required,notEmpty"`

	ShutdownTimeout time.Duration `env:"SHUTDOWN_TIMEOUT" envDefault:"20s"`
}

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
		listeningAddress("RECEIVER_HEALTH_ADDR", c.HealthAddr),
		noBlankEntries("KAFKA_BROKERS", c.KafkaBrokers),
		connectionURL("POSTGRES_DSN", c.PostgresDSN, "postgres", "postgresql"),
		connectionURL("REDIS_URL", c.RedisURL, "redis", "rediss"),
		positiveDuration("SHUTDOWN_TIMEOUT", c.ShutdownTimeout),
	)
}
