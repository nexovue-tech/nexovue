package logging_test

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
)

// knownSecret is planted in every place a credential can enter a log line. It
// must not survive into the output through any of them.
const knownSecret = "hunter2-do-not-log-this"

func TestJSONLineUsesTheSharedFieldNames(t *testing.T) {
	var out bytes.Buffer
	logger := logging.New(logging.Options{
		Level:   logging.LevelInfo,
		Format:  logging.FormatJSON,
		Service: "receiver",
		Env:     "development",
		Output:  &out,
	})

	ctx := logging.WithFields(context.Background(), logging.Fields{
		TraceID:       "4bf92f3577b34da6a3ce929d0e0e4736",
		SpanID:        "00f067aa0ba902b7",
		CorrelationID: "corr-1",
		OrgID:         "org-1",
		ProjectID:     "proj-1",
	})
	logger.InfoContext(ctx, "envelope produced")

	var line map[string]any
	if err := json.Unmarshal(out.Bytes(), &line); err != nil {
		t.Fatalf("log line is not JSON: %v (%q)", err, out.String())
	}

	want := map[string]string{
		logging.FieldMessage:       "envelope produced",
		logging.FieldLevel:         "info",
		logging.FieldService:       "receiver",
		logging.FieldEnv:           "development",
		logging.FieldTraceID:       "4bf92f3577b34da6a3ce929d0e0e4736",
		logging.FieldSpanID:        "00f067aa0ba902b7",
		logging.FieldCorrelationID: "corr-1",
		logging.FieldOrgID:         "org-1",
		logging.FieldProjectID:     "proj-1",
	}
	for field, value := range want {
		if line[field] != value {
			t.Errorf("%s = %v, want %q", field, line[field], value)
		}
	}
	if _, ok := line[logging.FieldTimestamp]; !ok {
		t.Errorf("missing %q; got %v", logging.FieldTimestamp, line)
	}
}

func TestSecretsNeverReachTheLog(t *testing.T) {
	formats := []string{logging.FormatJSON, logging.FormatConsole}
	attributes := map[string]string{
		"password":     knownSecret,
		"api_key":      knownSecret,
		"session_id":   knownSecret,
		"postgres_dsn": "postgresql://nexovue:" + knownSecret + "@db:5432/nexovue",
		"redis_url":    "redis://default:" + knownSecret + "@cache:6379/0",
	}

	for _, format := range formats {
		for key, value := range attributes {
			t.Run(format+"/"+key, func(t *testing.T) {
				var out bytes.Buffer
				logger := logging.New(logging.Options{
					Level:   logging.LevelDebug,
					Format:  format,
					Service: "receiver",
					Env:     "development",
					Output:  &out,
				})
				logger.Info("connecting", key, value)

				if strings.Contains(out.String(), knownSecret) {
					t.Fatalf("secret leaked into the log line: %q", out.String())
				}
				if !strings.Contains(out.String(), logging.Placeholder) {
					t.Errorf("expected the redaction placeholder, got %q", out.String())
				}
			})
		}
	}
}

func TestRedactCredentialsKeepsTheHostAndDropsThePassword(t *testing.T) {
	got := logging.RedactCredentials("postgresql://nexovue:" + knownSecret + "@db:5432/nexovue")

	if strings.Contains(got, knownSecret) {
		t.Fatalf("RedactCredentials kept the password: %q", got)
	}
	if !strings.Contains(got, "db:5432") {
		t.Errorf("RedactCredentials dropped the host, which is what operators need: %q", got)
	}
}

func TestLevelBelowThresholdIsDropped(t *testing.T) {
	var out bytes.Buffer
	logger := logging.New(logging.Options{
		Level:   logging.LevelWarn,
		Format:  logging.FormatJSON,
		Service: "receiver",
		Env:     "production",
		Output:  &out,
	})

	logger.Info("state change nobody asked for")

	if out.Len() != 0 {
		t.Errorf("info survived a warn threshold: %q", out.String())
	}
}
