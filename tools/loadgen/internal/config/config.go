// Package config reads the load generator's settings from the environment.
//
// Everything the run needs is a variable with a working default, so `just load`
// measures the local dev stack with no arguments and CI can retarget it without
// a code change.
package config

import (
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// Defaults describe one sustained run against the local dev stack.
//
// The offered span rate sits just above the 10,000 spans/sec budget so a passing
// run proves the budget rather than merely approaching it, and the batch size is
// the OpenTelemetry SDK's own default, which is the request shape a customer
// actually sends.
const (
	defaultEndpoint    = "http://localhost:14318"
	defaultIngestKey   = "nvk_live_devlocaldevlocaldevloc"
	defaultDuration    = "60s"
	defaultWorkers     = "8"
	defaultSpanRate    = "12000"
	defaultBatchSize   = "512"
	defaultChildSpans  = "4"
	defaultServiceName = "loadgen"
	defaultProxyAddr   = "127.0.0.1:0"
	defaultClickHouse  = "http://localhost:18123"
	defaultCHUser      = "nexovue"
	// The dev stack's ClickHouse password, published in docker-compose.yaml.
	defaultCHPassword   = "nexovue_dev" //nolint:gosec // G101: dev-stack default, not a secret
	defaultCHDatabase   = "nexovue"
	defaultLagSample    = "200ms"
	defaultLagPoll      = "250ms"
	defaultLagDrain     = "60s"
	defaultRequestLimit = "10s"
)

// ClickHouse is where the lag measurement looks for the spans it sent.
type ClickHouse struct {
	URL      string
	User     string
	Password string
	Database string
}

// Config is the load generator's complete settings surface.
type Config struct {
	// Endpoint is the receiver's OTLP/HTTP base URL, without the signal path.
	Endpoint  string
	IngestKey string

	Duration time.Duration
	Workers  int
	// SpanRate is the offered load in spans per second across all workers.
	// Zero removes the throttle and lets the generator send as fast as it can.
	SpanRate    float64
	BatchSize   int
	ChildSpans  int
	ServiceName string
	// RequestTimeout bounds one export, so a wedged receiver ends the run
	// instead of hanging it.
	RequestTimeout time.Duration

	// ProxyAddr is where the measuring proxy listens. Port 0 takes any free
	// port, which is what keeps concurrent runs from colliding.
	ProxyAddr string

	ClickHouse ClickHouse
	// LagSampleInterval spaces out the traces followed into storage, so the
	// samples span the whole run rather than clustering at its start.
	LagSampleInterval time.Duration
	// LagPollInterval is also the resolution of every lag figure reported.
	LagPollInterval time.Duration
	// LagDrainTimeout is how long to keep looking for spans after the load
	// stops before calling them lost.
	LagDrainTimeout time.Duration
}

// Load reads the environment, failing rather than starting half-configured.
func Load() (Config, error) {
	runDuration, err := positiveDuration("LOADGEN_DURATION", defaultDuration)
	if err != nil {
		return Config{}, err
	}
	workers, err := positiveInt("LOADGEN_WORKERS", defaultWorkers)
	if err != nil {
		return Config{}, err
	}
	spanRate, err := nonNegativeFloat("LOADGEN_SPAN_RATE", defaultSpanRate)
	if err != nil {
		return Config{}, err
	}
	batchSize, err := positiveInt("LOADGEN_BATCH_SIZE", defaultBatchSize)
	if err != nil {
		return Config{}, err
	}
	childSpans, err := positiveInt("LOADGEN_CHILD_SPANS", defaultChildSpans)
	if err != nil {
		return Config{}, err
	}
	requestTimeout, err := positiveDuration("LOADGEN_REQUEST_TIMEOUT", defaultRequestLimit)
	if err != nil {
		return Config{}, err
	}
	lagSample, err := positiveDuration("LOADGEN_LAG_SAMPLE_INTERVAL", defaultLagSample)
	if err != nil {
		return Config{}, err
	}
	lagPoll, err := positiveDuration("LOADGEN_LAG_POLL_INTERVAL", defaultLagPoll)
	if err != nil {
		return Config{}, err
	}
	lagDrain, err := positiveDuration("LOADGEN_LAG_DRAIN_TIMEOUT", defaultLagDrain)
	if err != nil {
		return Config{}, err
	}

	cfg := Config{
		Endpoint:          env("LOADGEN_ENDPOINT", defaultEndpoint),
		IngestKey:         env("LOADGEN_INGEST_KEY", defaultIngestKey),
		Duration:          runDuration,
		Workers:           workers,
		SpanRate:          spanRate,
		BatchSize:         batchSize,
		ChildSpans:        childSpans,
		ServiceName:       env("LOADGEN_SERVICE_NAME", defaultServiceName),
		RequestTimeout:    requestTimeout,
		ProxyAddr:         env("LOADGEN_PROXY_ADDR", defaultProxyAddr),
		LagSampleInterval: lagSample,
		LagPollInterval:   lagPoll,
		LagDrainTimeout:   lagDrain,
		ClickHouse: ClickHouse{
			URL:      env("LOADGEN_CLICKHOUSE_URL", defaultClickHouse),
			User:     env("LOADGEN_CLICKHOUSE_USER", defaultCHUser),
			Password: env("LOADGEN_CLICKHOUSE_PASSWORD", defaultCHPassword),
			Database: env("LOADGEN_CLICKHOUSE_DATABASE", defaultCHDatabase),
		},
	}

	if err := cfg.validate(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

// SpansPerTrace is how many spans one generated trace carries: the parent plus
// its children. It converts an offered span rate into telemetrygen's own units.
func (c Config) SpansPerTrace() int {
	return c.ChildSpans + 1
}

// RatePerWorker converts the offered span rate into telemetrygen's per-worker
// throttle, which counts every span it emits, parents included.
func (c Config) RatePerWorker() float64 {
	if c.SpanRate == 0 {
		return 0
	}
	return c.SpanRate / float64(c.Workers)
}

func (c Config) validate() error {
	if strings.TrimSpace(c.IngestKey) == "" {
		return fmt.Errorf("LOADGEN_INGEST_KEY: must not be empty; " +
			"a run without a key measures a receiver we do not ship")
	}
	if _, err := ParseEndpoint(c.Endpoint); err != nil {
		return err
	}
	if _, err := url.Parse(c.ClickHouse.URL); err != nil {
		return fmt.Errorf("LOADGEN_CLICKHOUSE_URL: got %q, want a URL", c.ClickHouse.URL)
	}
	return nil
}

// Endpoint is a receiver address split into the parts an OTLP exporter needs.
type Endpoint struct {
	// HostPort is the exporter's endpoint, which OTLP states without a scheme.
	HostPort string
	// Insecure reports plain HTTP, which the exporter must be told explicitly.
	Insecure bool
}

// ParseEndpoint splits an OTLP base URL into exporter terms.
func ParseEndpoint(raw string) (Endpoint, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Host == "" {
		return Endpoint{}, fmt.Errorf("LOADGEN_ENDPOINT: got %q, want a URL like http://host:port", raw)
	}
	switch parsed.Scheme {
	case "http":
		return Endpoint{HostPort: parsed.Host, Insecure: true}, nil
	case "https":
		return Endpoint{HostPort: parsed.Host, Insecure: false}, nil
	default:
		return Endpoint{}, fmt.Errorf("LOADGEN_ENDPOINT: got scheme %q, want http or https", parsed.Scheme)
	}
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// positiveDuration rejects zero as well as garbage: every duration this tool
// reads is an interval that must actually elapse.
func positiveDuration(key, fallback string) (time.Duration, error) {
	raw := env(key, fallback)
	value, err := time.ParseDuration(raw)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s: got %q, want a positive duration such as 60s", key, raw)
	}
	return value, nil
}

func positiveInt(key, fallback string) (int, error) {
	raw := env(key, fallback)
	value, err := strconv.Atoi(raw)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s: got %q, want a positive whole number", key, raw)
	}
	return value, nil
}

func nonNegativeFloat(key, fallback string) (float64, error) {
	raw := env(key, fallback)
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || value < 0 {
		return 0, fmt.Errorf("%s: got %q, want a non-negative number", key, raw)
	}
	return value, nil
}
