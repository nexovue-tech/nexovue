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
		"RECEIVER_HEALTH_ADDR", "KAFKA_BROKERS", "POSTGRES_DSN", "REDIS_URL",
		"SHUTDOWN_TIMEOUT",
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
		"unknown environment":  {"ENV", "prod"},
		"unknown log level":    {"LOG_LEVEL", "verbose"},
		"unknown log format":   {"LOG_FORMAT", "logfmt"},
		"address without port": {"RECEIVER_HEALTH_ADDR", "localhost"},
		"blank broker":         {"KAFKA_BROKERS", "localhost:9092, "},
		"zero timeout":         {"SHUTDOWN_TIMEOUT", "0s"},
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
