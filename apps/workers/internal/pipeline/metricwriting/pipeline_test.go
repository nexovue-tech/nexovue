package metricwriting

import (
	"bytes"
	"io"
	"log/slog"
	"strings"
	"testing"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/config"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/proberdoor"
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

// A forged synthetic reading is dropped, never silently: the batch's drops are
// counted for the heartbeat and named in one warning, with the tenant and door
// that sent them (browser-door decision 4).
func TestDroppedSyntheticReadingsAreCountedInALogLine(t *testing.T) {
	var captured bytes.Buffer
	pipeline := &Pipeline{log: slog.New(slog.NewJSONHandler(&captured, nil))}

	pipeline.reportSyntheticRefused(t.Context(), proberdoor.Refusals{
		Count: 3, TenantID: testTenant, ProjectID: testProject, Door: nexovuev1.Door_DOOR_RUM,
	})
	pipeline.reportSyntheticRefused(t.Context(), proberdoor.Refusals{})

	if pipeline.syntheticRefused != 3 {
		t.Errorf("synthetic_points_refused = %d, want 3", pipeline.syntheticRefused)
	}
	lines := strings.Split(strings.TrimSpace(captured.String()), "\n")
	if len(lines) != 1 {
		t.Fatalf("logged %d lines, want one warning for the batch that dropped points: %q", len(lines), lines)
	}
	for _, want := range []string{`"level":"WARN"`, `"refused":3`, `"tenant_id":"` + testTenant + `"`, `"door":"DOOR_RUM"`} {
		if !strings.Contains(lines[0], want) {
			t.Errorf("the warning %s does not carry %s", lines[0], want)
		}
	}
}
