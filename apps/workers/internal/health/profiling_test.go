package health_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/health"
)

func statusOf(t *testing.T, handler http.Handler, path string) int {
	t.Helper()
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
	return recorder.Code
}

func TestProfilesAreAbsentUntilAskedFor(t *testing.T) {
	handler := newHandler(healthy("clickhouse"))

	if status := statusOf(t, handler, health.ProfilingPath+"heap"); status != http.StatusNotFound {
		t.Errorf("status = %d, want %d: profiles must not be served by default",
			status, http.StatusNotFound)
	}
}

func TestProfilingServesTheHeapAndKeepsTheProbes(t *testing.T) {
	handler := health.WithProfiling(newHandler(healthy("clickhouse")))

	// debug=1 renders the profile as text, so the body is readable and the
	// request does not depend on a protobuf decoder to prove it answered.
	if status := statusOf(t, handler, health.ProfilingPath+"heap?debug=1"); status != http.StatusOK {
		t.Errorf("heap profile status = %d, want %d", status, http.StatusOK)
	}
	if status := statusOf(t, handler, health.ProfilingPath); status != http.StatusOK {
		t.Errorf("profile index status = %d, want %d", status, http.StatusOK)
	}

	status, report := get(t, handler, health.ReadinessPath)
	if status != http.StatusOK || report.Status != health.StatusOK {
		t.Errorf("readiness = %d/%q with profiling mounted, want %d/%q",
			status, report.Status, http.StatusOK, health.StatusOK)
	}
}
