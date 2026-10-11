package health_test

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/health"
)

func TestMetricsAreServedBesideTheProbes(t *testing.T) {
	registry := prometheus.NewRegistry()
	events := prometheus.NewCounter(prometheus.CounterOpts{Name: "nexovue_test_events_total", Help: "test"})
	registry.MustRegister(events)
	events.Inc()
	handler := health.WithMetrics(health.Handler(buildinfo.For("receiver"), health.NewRegistry()), registry)

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
