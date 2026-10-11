package health_test

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/health"
)

func TestMetricsAreServedBesideTheProbes(t *testing.T) {
	registry := prometheus.NewRegistry()
	events := prometheus.NewCounter(prometheus.CounterOpts{Name: "nexovue_test_events_total", Help: "test"})
	registry.MustRegister(events)
	events.Inc()
	handler := health.WithMetrics(health.Handler(buildinfo.For("worker"), health.NewRegistry(), health.NewRegistry()), registry)

	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, health.MetricsPath, nil))
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), "nexovue_test_events_total 1") {
		t.Fatalf("metrics: %d %q", recorder.Code, recorder.Body.String())
	}

	probe := httptest.NewRecorder()
	handler.ServeHTTP(probe, httptest.NewRequest(http.MethodGet, health.LivenessPath, nil))
	if probe.Code != http.StatusOK {
		t.Fatalf("liveness behind the metrics route: %d", probe.Code)
	}
}

func TestEveryReadinessCheckIsReportedAsUpOrDown(t *testing.T) {
	registry := health.NewRegistry()
	registry.Register(health.Check{Name: "kafka", Probe: func(context.Context) error { return nil }})
	registry.Register(health.Check{Name: "log-writing", Probe: func(context.Context) error {
		return errors.New("stopped")
	}})

	want := `
# HELP nexovue_worker_check_up Whether each readiness check passes: every pipeline, meter and dependency.
# TYPE nexovue_worker_check_up gauge
nexovue_worker_check_up{check="kafka"} 1
nexovue_worker_check_up{check="log-writing"} 0
`
	if err := testutil.CollectAndCompare(registry.Collector(), strings.NewReader(want)); err != nil {
		t.Fatal(err)
	}
}
