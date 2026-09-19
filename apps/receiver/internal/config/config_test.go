package config_test

import (
	"os"
	"strings"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/config"
)

// completeEnvironment is the smallest environment the receiver will boot with.
func completeEnvironment() map[string]string {
	return map[string]string{
		"ENV":           config.EnvDevelopment,
		"KAFKA_BROKERS": "localhost:9092",
		"POSTGRES_DSN":  "postgresql://nexovue:secret@localhost:5432/nexovue",
		"REDIS_URL":     "redis://localhost:6379/0",
	}
}

// applyEnvironment installs exactly the given variables and removes every other
// variable this package reads, so one test cannot inherit another's leftovers.
func applyEnvironment(t *testing.T, values map[string]string) {
	t.Helper()
	known := []string{
		"ENV", "LOG_LEVEL", "LOG_FORMAT", "RECEIVER_OTLP_HTTP_ADDR",
		"RECEIVER_OTLP_GRPC_ADDR", "RECEIVER_HEALTH_ADDR", "KAFKA_BROKERS", "POSTGRES_DSN", "REDIS_URL",
		"KAFKA_SPANS_TOPIC", "KAFKA_LOGS_TOPIC", "KAFKA_METRICS_TOPIC",
		"RECEIVER_MAX_BODY_BYTES", "RECEIVER_MAX_DECODED_BODY_BYTES",
		"RECEIVER_API_KEY_CACHE_TTL", "RECEIVER_API_KEY_CACHE_MAX_ENTRIES",
		"RECEIVER_RATE_LIMIT_PER_SECOND", "RECEIVER_POSTGRES_MAX_CONNS",
		"RECEIVER_POSTGRES_MIN_CONNS", "SHUTDOWN_TIMEOUT",
		"RECEIVER_KAFKA_MAX_BUFFERED_BYTES", "RECEIVER_PRODUCE_TIMEOUT",
		"RECEIVER_INGEST_SUMMARY_INTERVAL",
	}
	for _, key := range known {
		if value, ok := values[key]; ok {
			t.Setenv(key, value)
			continue
		}
		t.Setenv(key, "") // registers the restore hook
		if err := os.Unsetenv(key); err != nil {
			t.Fatalf("unsetting %s: %v", key, err)
		}
	}
}

func TestLoadAcceptsTheMinimumEnvironment(t *testing.T) {
	applyEnvironment(t, completeEnvironment())

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load() = %v, want no error", err)
	}
	if cfg.LogFormat != "json" {
		t.Errorf("LogFormat = %q, want the production-safe default %q", cfg.LogFormat, "json")
	}
	if cfg.HealthAddr == "" {
		t.Error("HealthAddr is empty; the probe listener must have a default")
	}
	// Compose publishes 4318 and 4317; the ports the protocol reserves are what
	// the receiver must bind without being told to.
	if cfg.OTLPHTTPAddr != ":4318" || cfg.OTLPGRPCAddr != ":4317" {
		t.Errorf("OTLP addresses = %q/%q, want the reserved %q/%q",
			cfg.OTLPHTTPAddr, cfg.OTLPGRPCAddr, ":4318", ":4317")
	}
	// Compose sets none of these, so every one of them needs a working default.
	topics := map[string]string{
		"KAFKA_SPANS_TOPIC":    cfg.SpansTopic,
		"KAFKA_LOGS_TOPIC":     cfg.LogsTopic,
		"KAFKA_METRICS_TOPIC":  cfg.MetricsTopic,
		"KAFKA_PROFILES_TOPIC": cfg.ProfilesTopic,
	}
	for variable, configured := range topics {
		want := map[string]string{
			"KAFKA_SPANS_TOPIC":    "spans-raw",
			"KAFKA_LOGS_TOPIC":     "logs-raw",
			"KAFKA_METRICS_TOPIC":  "metrics-raw",
			"KAFKA_PROFILES_TOPIC": "profiles-raw",
		}[variable]
		if configured != want {
			t.Errorf("%s = %q, want the topic deploy/kafka/topics.sh creates, %q",
				variable, configured, want)
		}
	}
	if cfg.MaxBodyBytes <= 0 || cfg.MaxDecodedBodyBytes < cfg.MaxBodyBytes {
		t.Errorf("body limits = %d/%d, want a usable pair",
			cfg.MaxBodyBytes, cfg.MaxDecodedBodyBytes)
	}
	if cfg.APIKeyCacheTTL <= 0 || cfg.APIKeyCacheTTL > config.MaxAPIKeyCacheTTL {
		t.Errorf("APIKeyCacheTTL = %s, want a revocation window within %s",
			cfg.APIKeyCacheTTL, config.MaxAPIKeyCacheTTL)
	}
	if cfg.RateLimitPerSecond <= 0 {
		t.Errorf("RateLimitPerSecond = %d, want a positive default", cfg.RateLimitPerSecond)
	}
	if cfg.ProfileSamplesPerSecond != 20_000 {
		t.Errorf("ProfileSamplesPerSecond = %d, want the recorded 20,000", cfg.ProfileSamplesPerSecond)
	}
	if cfg.PostgresMinConns <= 0 || cfg.PostgresMinConns > cfg.PostgresMaxConns {
		t.Errorf("pool bounds = %d/%d, want a usable pair",
			cfg.PostgresMinConns, cfg.PostgresMaxConns)
	}
}

func TestLoadNamesEachMissingRequiredVariable(t *testing.T) {
	for _, missing := range []string{"ENV", "KAFKA_BROKERS", "POSTGRES_DSN", "REDIS_URL"} {
		t.Run(missing, func(t *testing.T) {
			values := completeEnvironment()
			delete(values, missing)
			applyEnvironment(t, values)

			_, err := config.Load()
			if err == nil {
				t.Fatalf("Load() succeeded without %s, want a boot failure", missing)
			}
			if !strings.Contains(err.Error(), missing) {
				t.Errorf("Load() = %q, want the message to name %s", err, missing)
			}
		})
	}
}

func TestLoadRejectsAnEmptySecret(t *testing.T) {
	values := completeEnvironment()
	values["POSTGRES_DSN"] = ""
	applyEnvironment(t, values)

	_, err := config.Load()
	if err == nil || !strings.Contains(err.Error(), "POSTGRES_DSN") {
		t.Fatalf("Load() = %v, want a failure naming POSTGRES_DSN", err)
	}
}

func TestLoadRejectsMalformedValues(t *testing.T) {
	cases := map[string]struct{ key, value string }{
		"unknown environment":                   {"ENV", "prod"},
		"unknown log level":                     {"LOG_LEVEL", "verbose"},
		"unknown log format":                    {"LOG_FORMAT", "logfmt"},
		"address without port":                  {"RECEIVER_HEALTH_ADDR", "localhost"},
		"grpc address without port":             {"RECEIVER_OTLP_GRPC_ADDR", "localhost"},
		"blank broker":                          {"KAFKA_BROKERS", "localhost:9092, "},
		"zero timeout":                          {"SHUTDOWN_TIMEOUT", "0s"},
		"blank spans topic":                     {"KAFKA_SPANS_TOPIC", "  "},
		"blank logs topic":                      {"KAFKA_LOGS_TOPIC", "  "},
		"blank metrics topic":                   {"KAFKA_METRICS_TOPIC", "  "},
		"zero body limit":                       {"RECEIVER_MAX_BODY_BYTES", "0"},
		"decoded cap below the wire cap":        {"RECEIVER_MAX_DECODED_BODY_BYTES", "1024"},
		"cache ttl past the revocation ceiling": {"RECEIVER_API_KEY_CACHE_TTL", "10m"},
		"zero cache ceiling":                    {"RECEIVER_API_KEY_CACHE_MAX_ENTRIES", "0"},
		"zero rate limit":                       {"RECEIVER_RATE_LIMIT_PER_SECOND", "0"},
		"zero produce timeout":                  {"RECEIVER_PRODUCE_TIMEOUT", "0s"},
		"zero summary interval":                 {"RECEIVER_INGEST_SUMMARY_INTERVAL", "0s"},
		// franz-go fails a record larger than its buffer outright, and no
		// retry can fix that — so a buffer that cannot hold the largest legal
		// batch is a refusal loop the sender can never escape.
		"buffer below the largest legal batch": {"RECEIVER_KAFKA_MAX_BUFFERED_BYTES", "1048576"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			values := completeEnvironment()
			values[tc.key] = tc.value
			applyEnvironment(t, values)

			_, err := config.Load()
			if err == nil {
				t.Fatalf("Load() accepted %s=%q", tc.key, tc.value)
			}
			if !strings.Contains(err.Error(), tc.key) {
				t.Errorf("Load() = %q, want the message to name %s", err, tc.key)
			}
		})
	}
}

func TestLoadBoundsTheProducerByDefault(t *testing.T) {
	applyEnvironment(t, completeEnvironment())

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load() = %v, want no error", err)
	}
	// franz-go buffers without limit and retries forever unless told otherwise,
	// so an unconfigured deployment has to arrive already bounded.
	if cfg.KafkaMaxBufferedBytes < cfg.MaxDecodedBodyBytes+config.EnvelopeOverheadBytes {
		t.Errorf("KafkaMaxBufferedBytes = %d, too small for the largest legal batch (%d)",
			cfg.KafkaMaxBufferedBytes, cfg.MaxDecodedBodyBytes+config.EnvelopeOverheadBytes)
	}
	if cfg.ProduceTimeout <= 0 {
		t.Error("ProduceTimeout is unset; a publish would have no deadline at all")
	}
	if cfg.IngestSummaryInterval <= 0 {
		t.Error("IngestSummaryInterval is unset; the receiver would log nothing while serving")
	}
}
