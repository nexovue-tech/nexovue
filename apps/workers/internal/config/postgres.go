package config

import (
	"errors"
	"net/url"
)

const postgresDSNVariable = "POSTGRES_DSN"

// Postgres is the control-plane connection the pipeline reads per-project
// configuration from.
//
// The DSN is optional, unlike the receiver's. A worker with no control-plane
// connection still masks — the default rules are compiled in — and still
// samples at the deployment default; what it loses is per-project overrides,
// which it says at boot rather than pretending it has them.
type Postgres struct {
	DSN      string `env:"POSTGRES_DSN"`
	MaxConns int32  `env:"WORKERS_POSTGRES_MAX_CONNS" envDefault:"4"`
	MinConns int32  `env:"WORKERS_POSTGRES_MIN_CONNS" envDefault:"1"`
}

// Configured reports whether a control-plane connection was given.
func (p Postgres) Configured() bool { return p.DSN != "" }

func (p Postgres) validate() error {
	if !p.Configured() {
		return nil
	}
	return errors.Join(
		postgresURL(postgresDSNVariable, p.DSN),
		positiveCount("WORKERS_POSTGRES_MAX_CONNS", int(p.MaxConns)),
		positiveCount("WORKERS_POSTGRES_MIN_CONNS", int(p.MinConns)),
		atMostCount("WORKERS_POSTGRES_MIN_CONNS", int(p.MinConns),
			"WORKERS_POSTGRES_MAX_CONNS", int(p.MaxConns)),
	)
}

// postgresURL checks the DSN at boot, so a typo fails the process rather than
// the first refresh half a minute later.
func postgresURL(variable, dsn string) error {
	parsed, err := url.Parse(dsn)
	if err != nil {
		return invalid(variable, "is not a URL: %v", err)
	}
	if parsed.Scheme != "postgres" && parsed.Scheme != "postgresql" {
		return invalid(variable, "got scheme %q, want postgres:// or postgresql://", parsed.Scheme)
	}
	if parsed.Host == "" {
		return invalid(variable, "has no host")
	}
	return nil
}
