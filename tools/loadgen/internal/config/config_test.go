package config

import (
	"testing"
	"time"
)

func TestLoadDefaultsToTheDevStack(t *testing.T) {
	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load with no environment set: %v", err)
	}

	if cfg.Endpoint != defaultEndpoint {
		t.Errorf("Endpoint = %q, want %q", cfg.Endpoint, defaultEndpoint)
	}
	// A default run must offer more than the 10,000 spans/sec budget, or a
	// passing result would prove nothing about it.
	if cfg.SpanRate <= 10000 {
		t.Errorf("SpanRate = %.0f, want more than the 10000 spans/sec budget", cfg.SpanRate)
	}
	if cfg.IngestKey == "" {
		t.Error("IngestKey is empty, which would bypass the receiver's auth gate")
	}
}

func TestLoadRejectsUnusableValues(t *testing.T) {
	cases := []struct {
		name  string
		key   string
		value string
	}{
		{"zero duration", "LOADGEN_DURATION", "0s"},
		{"unparseable duration", "LOADGEN_DURATION", "a while"},
		{"zero workers", "LOADGEN_WORKERS", "0"},
		{"negative rate", "LOADGEN_SPAN_RATE", "-1"},
		{"zero batch", "LOADGEN_BATCH_SIZE", "0"},
		{"empty key", "LOADGEN_INGEST_KEY", " "},
		{"endpoint without scheme", "LOADGEN_ENDPOINT", "localhost:14318"},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Setenv(c.key, c.value)
			if _, err := Load(); err == nil {
				t.Errorf("Load with %s=%q succeeded, want a refusal", c.key, c.value)
			}
		})
	}
}

func TestParseEndpointSplitsSchemeFromHost(t *testing.T) {
	cases := []struct {
		raw          string
		wantHostPort string
		wantInsecure bool
	}{
		{"http://localhost:14318", "localhost:14318", true},
		{"https://ingest.nexovue.io", "ingest.nexovue.io", false},
	}

	for _, c := range cases {
		got, err := ParseEndpoint(c.raw)
		if err != nil {
			t.Fatalf("ParseEndpoint(%q): %v", c.raw, err)
		}
		if got.HostPort != c.wantHostPort || got.Insecure != c.wantInsecure {
			t.Errorf("ParseEndpoint(%q) = %+v, want {%s %t}",
				c.raw, got, c.wantHostPort, c.wantInsecure)
		}
	}
}

func TestParseEndpointRejectsOtherSchemes(t *testing.T) {
	if _, err := ParseEndpoint("grpc://localhost:4317"); err == nil {
		t.Error("ParseEndpoint accepted a non-HTTP scheme, want a refusal")
	}
}

// telemetrygen throttles each worker separately and counts every span, so the
// offered rate has to be divided before it is handed over.
func TestRatePerWorkerDividesTheOfferedRate(t *testing.T) {
	cfg := Config{SpanRate: 12000, Workers: 8}
	if got := cfg.RatePerWorker(); got != 1500 {
		t.Errorf("RatePerWorker() = %.0f, want 1500", got)
	}
}

func TestRatePerWorkerKeepsZeroUnthrottled(t *testing.T) {
	cfg := Config{SpanRate: 0, Workers: 8}
	if got := cfg.RatePerWorker(); got != 0 {
		t.Errorf("RatePerWorker() = %.0f, want 0 so the generator stays unthrottled", got)
	}
}

func TestSpansPerTraceCountsTheParent(t *testing.T) {
	if got := (Config{ChildSpans: 4}).SpansPerTrace(); got != 5 {
		t.Errorf("SpansPerTrace() = %d, want 5", got)
	}
}

func TestLagResolutionDefaultsBelowTheBudget(t *testing.T) {
	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	// The lag budget is 15s at p99; a poll interval anywhere near it would
	// make the measurement meaningless.
	if cfg.LagPollInterval > time.Second {
		t.Errorf("LagPollInterval = %s, too coarse for a 15s budget", cfg.LagPollInterval)
	}
}
