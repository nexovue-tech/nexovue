package health_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/health"
)

func statusOf(t *testing.T, handler http.Handler, path string) int {
	t.Helper()
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
	return recorder.Code
}

// The default matters more here than on the worker: this process also serves
// the two OTLP ports, so a profiler mounted by accident is a profiler on a
// service the internet already talks to. It is off until a deployment says so.
func TestProfilesAreAbsentUntilAskedFor(t *testing.T) {
	handler := newHandler(healthy("kafka"))

	if status := statusOf(t, handler, health.ProfilingPath+"heap"); status != http.StatusNotFound {
		t.Errorf("status = %d, want %d: profiles must not be served by default",
			status, http.StatusNotFound)
	}
}

func TestProfilingServesTheHeapAndKeepsTheProbes(t *testing.T) {
	handler := health.WithProfiling(newHandler(healthy("kafka")))

	// debug=1 renders the profile as text, so the body is readable and the
	// request does not depend on a protobuf decoder to prove it answered.
	if status := statusOf(t, handler, health.ProfilingPath+"heap?debug=1"); status != http.StatusOK {
		t.Errorf("heap profile status = %d, want %d", status, http.StatusOK)
	}
	if status := statusOf(t, handler, health.ProfilingPath); status != http.StatusOK {
		t.Errorf("profile index status = %d, want %d", status, http.StatusOK)
	}

	// The probes are what the admin listener is for. Mounting profiles in front
	// of them must not take a container's healthcheck away.
	status, report := get(t, handler, health.ReadinessPath)
	if status != http.StatusOK || report.Status != health.StatusOK {
		t.Errorf("readiness = %d/%q with profiling mounted, want %d/%q",
			status, report.Status, http.StatusOK, health.StatusOK)
	}
	if status := statusOf(t, handler, health.LivenessPath); status != http.StatusOK {
		t.Errorf("liveness status = %d, want %d", status, http.StatusOK)
	}
}
