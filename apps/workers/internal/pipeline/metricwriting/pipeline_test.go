package metricwriting

import (
	"io"
	"log/slog"
	"strings"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
)

func discardLogger(t *testing.T) *slog.Logger {
	t.Helper()
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// Metric attributes carry the same customer data span attributes do, and the
// pipeline order invariant is that an unmasked value never reaches the store.
// A worker that cannot mask them must not start rather than write them.
func TestTheMetricPipelineRefusesToRunWithoutAMasker(t *testing.T) {
	_, err := New(config.Config{}, nil, discardLogger(t), Options{})
	if err == nil {
		t.Fatal("New() succeeded with no masker; unmasked attributes would reach the store")
	}
	if !strings.Contains(err.Error(), "masker") {
		t.Errorf("New() error = %q, want it to name the missing masker", err)
	}
}

// A project the refresher has not seen keeps the deployment default rather than
// zero, which would expire its rows on arrival.
func TestRetentionFallsBackToTheDeploymentDefault(t *testing.T) {
	cfg := config.Config{Retention: config.Retention{RawDays: 30, DerivedDays: 395}}

	_, derived := retentionSource(cfg, Options{}).Retention("a-project-nobody-configured")
	if derived != 395 {
		t.Errorf("derived retention = %d, want the deployment default of 395", derived)
	}
}
