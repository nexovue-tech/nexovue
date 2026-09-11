package config_test

import (
	"os"
	"strings"
	"testing"
	"time"

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
		"WORKERS_SPANS_BATCH_MAX_MESSAGES", "WORKERS_SPANS_BATCH_MAX_DELAY",
		"WORKERS_SPANS_DLQ_TOPIC", "POSTGRES_DSN", "WORKERS_POSTGRES_MAX_CONNS",
		"WORKERS_POSTGRES_MIN_CONNS", "WORKERS_PROJECT_CONFIG_REFRESH",
		"WORKERS_SAMPLING_WINDOW", "WORKERS_SAMPLING_DECISION_TTL",
		"WORKERS_SAMPLING_SLOW_THRESHOLD", "WORKERS_SAMPLING_HEALTHY_PERCENT",
		"WORKERS_SAMPLING_FIRST_TRACES",
		"WORKERS_SAMPLING_MAX_TRACES", "WORKERS_SAMPLING_MAX_SPANS",
		"WORKERS_METRICS_LATE_GRACE",
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
		"zero batch messages":  {"WORKERS_SPANS_BATCH_MAX_MESSAGES", "0"},
		"zero batch delay":     {"WORKERS_SPANS_BATCH_MAX_DELAY", "0s"},
		"wrong dsn scheme":     {"CLICKHOUSE_DSN", "postgresql://h:9000/nexovue"},
		"dsn without database": {"CLICKHOUSE_DSN", "clickhouse://h:9000"},
		"dsn without host":     {"CLICKHOUSE_DSN", "clickhouse:///nexovue"},
		"zero pool":            {"CLICKHOUSE_MAX_CONNS", "0"},
		"blank dlq topic":      {"WORKERS_SPANS_DLQ_TOPIC", " "},
		// A dead-letter topic equal to its source replays a poison message onto
		// itself forever.
		"dlq topic equal to the source": {"WORKERS_SPANS_DLQ_TOPIC", "spans-raw"},
		"wrong postgres scheme":         {"POSTGRES_DSN", "mysql://h:5432/nexovue"},
		"postgres dsn without host":     {"POSTGRES_DSN", "postgres:///nexovue"},
		"zero sampling window":          {"WORKERS_SAMPLING_WINDOW", "0s"},
		"zero decision ttl":             {"WORKERS_SAMPLING_DECISION_TTL", "0s"},
		"zero slow threshold":           {"WORKERS_SAMPLING_SLOW_THRESHOLD", "0s"},
		"percentage above a hundred":    {"WORKERS_SAMPLING_HEALTHY_PERCENT", "101"},
		"negative percentage":           {"WORKERS_SAMPLING_HEALTHY_PERCENT", "-1"},
		"negative first traces":         {"WORKERS_SAMPLING_FIRST_TRACES", "-1"},
		"unbounded trace buffer":        {"WORKERS_SAMPLING_MAX_TRACES", "0"},
		"unbounded span buffer":         {"WORKERS_SAMPLING_MAX_SPANS", "0"},
		"zero config refresh":           {"WORKERS_PROJECT_CONFIG_REFRESH", "0s"},
		"zero metrics grace":            {"WORKERS_METRICS_LATE_GRACE", "0s"},
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
	if cfg.Spans.MaxMessages <= 0 {
		t.Errorf("Spans.MaxMessages = %d, want a positive default", cfg.Spans.MaxMessages)
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

// Compose sets none of these either, so the M2 stages must all boot on their
// defaults — including the sampling policy, which is the shipped behaviour.
func TestPipelineStageDefaultsMatchTheRecordedDecisions(t *testing.T) {
	applyEnvironment(t, completeEnvironment())

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load() = %v, want no error", err)
	}

	if cfg.Spans.DLQTopic != "spans-dlq" {
		t.Errorf("Spans.DLQTopic = %q, want spans-dlq: it must match deploy/kafka/topics.sh",
			cfg.Spans.DLQTopic)
	}
	// The window is P1-21's; 10s is what the p99 < 15s budget leaves.
	if cfg.Sampling.Window != 10*time.Second {
		t.Errorf("Sampling.Window = %s, want 10s", cfg.Sampling.Window)
	}
	if cfg.Sampling.HealthyPercent != 10 {
		t.Errorf("Sampling.HealthyPercent = %d, want the policy's 10", cfg.Sampling.HealthyPercent)
	}
	if cfg.Sampling.SlowThreshold != 2*time.Second {
		t.Errorf("Sampling.SlowThreshold = %s, want the policy's 2s", cfg.Sampling.SlowThreshold)
	}
	// The carve-out that keeps a new customer's first span out of the 10%.
	if cfg.Sampling.FirstTraces != 100 {
		t.Errorf("Sampling.FirstTraces = %d, want 100", cfg.Sampling.FirstTraces)
	}
	if cfg.Sampling.MaxTraces <= 0 || cfg.Sampling.MaxSpans <= 0 {
		t.Error("the sampling buffer has no default bound; an unbounded map is an outage waiting")
	}
	if cfg.Metrics.LateGrace <= 0 {
		t.Errorf("Metrics.LateGrace = %s, want a positive default", cfg.Metrics.LateGrace)
	}
	if cfg.Masking.ProjectRefresh <= 0 {
		t.Errorf("Masking.ProjectRefresh = %s, want a positive default", cfg.Masking.ProjectRefresh)
	}
}

// The receiver requires POSTGRES_DSN; the worker must not. Without it the
// stages run on their built-in defaults rather than refusing to start.
func TestAWorkerBootsWithoutAControlPlaneConnection(t *testing.T) {
	applyEnvironment(t, completeEnvironment())

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load() = %v, want no error", err)
	}
	if cfg.Postgres.Configured() {
		t.Error("Postgres.Configured() is true with no POSTGRES_DSN set")
	}
}
