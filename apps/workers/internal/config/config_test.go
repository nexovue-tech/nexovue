package config_test

import (
	"os"
	"strings"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
)

// completeEnvironment is the smallest environment a worker will boot with.
func completeEnvironment() map[string]string {
	return map[string]string{
		"ENV":            config.EnvDevelopment,
		"KAFKA_BROKERS":  "localhost:9092",
		"CLICKHOUSE_DSN": "clickhouse://nexovue:secret@localhost:9000/nexovue",
	}
}

// applyEnvironment installs exactly the given variables and removes every other
// variable this package reads, so one test cannot inherit another's leftovers.
func applyEnvironment(t *testing.T, values map[string]string) {
	t.Helper()
	known := []string{
		"ENV", "LOG_LEVEL", "LOG_FORMAT", "WORKERS_HEALTH_ADDR", "KAFKA_BROKERS",
		"CLICKHOUSE_DSN", "CLICKHOUSE_MAX_CONNS", "SHUTDOWN_TIMEOUT",
		"WORKER_PIPELINES", "KAFKA_SPANS_TOPIC", "WORKERS_CONSUMER_GROUP",
		"WORKERS_SPANS_BATCH_MAX_ROWS", "WORKERS_SPANS_BATCH_MAX_DELAY",
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

func TestLoadParsesTheClickHouseDSN(t *testing.T) {
	applyEnvironment(t, completeEnvironment())

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load() = %v, want no error", err)
	}
	want := config.ClickHouseTarget{
		Address:  "localhost:9000",
		Database: "nexovue",
		User:     "nexovue",
		Password: "secret",
	}
	if cfg.ClickHouse != want {
		t.Errorf("ClickHouse = %+v, want %+v", cfg.ClickHouse, want)
	}
	if cfg.ClickHouseMaxConns <= 0 {
		t.Error("the pool size must have an explicit default, not zero")
	}
}

func TestLoadNamesEachMissingRequiredVariable(t *testing.T) {
	for _, missing := range []string{"ENV", "KAFKA_BROKERS", "CLICKHOUSE_DSN"} {
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

func TestLoadRejectsMalformedValues(t *testing.T) {
	cases := map[string]struct{ key, value string }{
		"unknown environment":  {"ENV", "prod"},
		"unknown log level":    {"LOG_LEVEL", "verbose"},
		"unknown log format":   {"LOG_FORMAT", "logfmt"},
		"address without port": {"WORKERS_HEALTH_ADDR", "localhost"},
		"unknown pipeline":     {"WORKER_PIPELINES", "trace-assembler"},
		"blank spans topic":    {"KAFKA_SPANS_TOPIC", " "},
		"blank consumer group": {"WORKERS_CONSUMER_GROUP", "  "},
		"zero batch rows":      {"WORKERS_SPANS_BATCH_MAX_ROWS", "0"},
		"zero batch delay":     {"WORKERS_SPANS_BATCH_MAX_DELAY", "0s"},
		"wrong dsn scheme":     {"CLICKHOUSE_DSN", "postgresql://h:9000/nexovue"},
		"dsn without database": {"CLICKHOUSE_DSN", "clickhouse://h:9000"},
		"dsn without host":     {"CLICKHOUSE_DSN", "clickhouse:///nexovue"},
		"zero pool":            {"CLICKHOUSE_MAX_CONNS", "0"},
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

// Compose sets none of these, so every one of them needs a working default.
func TestSpanIngestDefaultsLetAWorkerBootUnconfigured(t *testing.T) {
	applyEnvironment(t, completeEnvironment())

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load() = %v, want no error", err)
	}

	if cfg.Spans.Topic != "spans-raw" {
		t.Errorf("Spans.Topic = %q, want spans-raw: it must match the receiver's default",
			cfg.Spans.Topic)
	}
	if cfg.Spans.ConsumerGroup == "" {
		t.Error("Spans.ConsumerGroup has no default; a worker would not join a group")
	}
	if cfg.Spans.MaxRows <= 0 {
		t.Errorf("Spans.MaxRows = %d, want a positive default", cfg.Spans.MaxRows)
	}
	// Without this the p99 lag budget rests on a batch that a quiet tenant
	// never fills.
	if cfg.Spans.MaxDelay <= 0 {
		t.Errorf("Spans.MaxDelay = %s, want a positive default", cfg.Spans.MaxDelay)
	}
}

func TestPipelinesDefaultToAllOfThem(t *testing.T) {
	applyEnvironment(t, completeEnvironment())

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load() = %v, want no error", err)
	}
	if len(cfg.Pipelines) != len(config.AllPipelines) {
		t.Errorf("Pipelines = %v, want all of %v", cfg.Pipelines, config.AllPipelines)
	}
}
