package main

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Defaults describe the local development stack, so `just gate-seed` needs no
// arguments. CI retargets any of them through the environment.
const (
	defaultEndpoint   = "http://localhost:14318"
	defaultIngestKey  = "nvk_live_devlocaldevlocaldevloc"
	defaultClickHouse = "http://localhost:18123"
	defaultCHUser     = "nexovue"
	// The development stack's ClickHouse password, published in docker-compose.yaml.
	defaultCHPassword = "nexovue_dev" //nolint:gosec // G101: dev-stack default, not a secret
	defaultCHDatabase = "nexovue"

	// **More than one page.** The isolation suite asks for a cursor, and a cursor
	// exists only when the first page did not exhaust the result — `MAX_PAGE` is
	// 50 there, so 50 traces would leave `next_cursor` null and the forged-cursor
	// test with nothing to forge against.
	defaultTraces = "80"
	// Exports per series. Cumulative counts have to rise across several points or
	// the window delta and the naive sum are the same number, which is the very
	// confusion `test_the_window_delta_is_far_below_the_number_of_points_stored`
	// exists to catch.
	defaultExports = "6"
	defaultSeries  = "3"

	// The API the gate's synthetic check is created through, signed in as the
	// development stack's seeded owner — the same account and defaults
	// tools/otel-demo/doors.py uses.
	defaultAPI         = "http://localhost:18000"
	defaultDevEmail    = "nexovue@nexovue.com"
	defaultDevPassword = "nexovue-dev" //nolint:gosec // G101: the dev stack's seeded login, not a secret

	// What the gate's check probes, as the probe workers reach it: the API's own
	// liveness endpoint. Something the stack always runs, so the synthetics suite
	// no longer depends on the OpenTelemetry demo. Through the Docker host's
	// published port, not the compose name `api`: the API refuses a check aimed
	// at one of our own service names (checks/targets.py, INTERNAL_HOSTNAMES),
	// and it is right to.
	defaultProbeTarget = "http://host.docker.internal:18000/healthz"

	// Generous: a check created this run is probed on its first scheduled turn,
	// up to a minute later.
	defaultWait     = "150s"
	defaultPoll     = "500ms"
	defaultTimeout  = "10s"
	defaultService  = "gateseed"
	defaultMetric   = "gateseed.request.duration"
	defaultDisabled = ""
)

// ClickHouse is where the seeder waits for what it sent to become readable.
type ClickHouse struct {
	URL      string
	User     string
	Password string
	Database string
}

// Config is the seeder's complete settings surface.
type Config struct {
	Endpoint  string
	IngestKey string
	ClickHouse

	// Traces is how many root spans, each its own trace, are sent.
	Traces int
	// Series is how many attribute combinations the histogram is split across,
	// and Exports how many cumulative points each of them reports.
	Series  int
	Exports int

	ServiceName string
	MetricName  string

	// API, DevEmail and DevPassword are how the gate's synthetic check is
	// created; ProbeTarget is the URL it probes.
	API         string
	DevEmail    string
	DevPassword string
	ProbeTarget string

	// Wait bounds how long the seeder waits for the ingest path to deliver what
	// it sent. Poll is how often it asks.
	Wait           time.Duration
	Poll           time.Duration
	RequestTimeout time.Duration

	// Disabled skips the whole run, for a gate deliberately run without docker.
	Disabled bool
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func positive(name, raw string) (int, error) {
	value, err := strconv.Atoi(raw)
	if err != nil || value < 1 {
		return 0, fmt.Errorf("%s must be a positive whole number, got %q", name, raw)
	}
	return value, nil
}

func duration(name, raw string) (time.Duration, error) {
	value, err := time.ParseDuration(raw)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive duration, got %q", name, raw)
	}
	return value, nil
}

// Load reads the settings, refusing a value it cannot use rather than falling
// back to a default and seeding something other than what was asked for.
func Load() (Config, error) {
	cfg := Config{
		Endpoint:  env("NEXOVUE_SEED_ENDPOINT", defaultEndpoint),
		IngestKey: env("NEXOVUE_SEED_INGEST_KEY", defaultIngestKey),
		ClickHouse: ClickHouse{
			URL:      env("NEXOVUE_SEED_CLICKHOUSE_URL", defaultClickHouse),
			User:     env("NEXOVUE_SEED_CLICKHOUSE_USER", defaultCHUser),
			Password: env("NEXOVUE_SEED_CLICKHOUSE_PASSWORD", defaultCHPassword),
			Database: env("NEXOVUE_SEED_CLICKHOUSE_DATABASE", defaultCHDatabase),
		},
		ServiceName: env("NEXOVUE_SEED_SERVICE", defaultService),
		MetricName:  env("NEXOVUE_SEED_METRIC", defaultMetric),
		API:         env("NEXOVUE_SEED_API_URL", defaultAPI),
		DevEmail:    env("NEXOVUE_DEV_EMAIL", defaultDevEmail),
		DevPassword: env("NEXOVUE_DEV_PASSWORD", defaultDevPassword),
		ProbeTarget: env("NEXOVUE_SEED_PROBE_TARGET", defaultProbeTarget),
		Disabled:    env("NEXOVUE_SKIP_LIVE_TESTS", defaultDisabled) != "",
	}

	var err error
	if cfg.Traces, err = positive("NEXOVUE_SEED_TRACES", env("NEXOVUE_SEED_TRACES", defaultTraces)); err != nil {
		return Config{}, err
	}
	if cfg.Series, err = positive("NEXOVUE_SEED_SERIES", env("NEXOVUE_SEED_SERIES", defaultSeries)); err != nil {
		return Config{}, err
	}
	if cfg.Exports, err = positive("NEXOVUE_SEED_EXPORTS", env("NEXOVUE_SEED_EXPORTS", defaultExports)); err != nil {
		return Config{}, err
	}
	if cfg.Wait, err = duration("NEXOVUE_SEED_WAIT", env("NEXOVUE_SEED_WAIT", defaultWait)); err != nil {
		return Config{}, err
	}
	if cfg.Poll, err = duration("NEXOVUE_SEED_POLL", env("NEXOVUE_SEED_POLL", defaultPoll)); err != nil {
		return Config{}, err
	}
	if cfg.RequestTimeout, err = duration("NEXOVUE_SEED_TIMEOUT", env("NEXOVUE_SEED_TIMEOUT", defaultTimeout)); err != nil {
		return Config{}, err
	}
	return cfg, nil
}
