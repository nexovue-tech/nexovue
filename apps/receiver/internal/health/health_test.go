package health_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/buildinfo"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/health"
)

func newHandler(checks ...health.Check) http.Handler {
	registry := health.NewRegistry()
	for _, check := range checks {
		registry.Register(check)
	}
	return health.Handler(buildinfo.For("receiver"), registry)
}

func get(t *testing.T, handler http.Handler, path string) (int, health.Report) {
	t.Helper()
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))

	var report health.Report
	if err := json.Unmarshal(recorder.Body.Bytes(), &report); err != nil {
		t.Fatalf("%s body is not JSON: %v (%q)", path, err, recorder.Body.String())
	}
	return recorder.Code, report
}

func failing(name string, err error) health.Check {
	return health.Check{Name: name, Probe: func(context.Context) error { return err }}
}

func healthy(name string) health.Check {
	return health.Check{Name: name, Probe: func(context.Context) error { return nil }}
}

func TestLivenessIgnoresDependencies(t *testing.T) {
	handler := newHandler(failing("kafka", errors.New("dial tcp: connection refused")))

	status, report := get(t, handler, health.LivenessPath)

	if status != http.StatusOK {
		t.Errorf("status = %d, want %d: liveness must not follow a dependency down",
			status, http.StatusOK)
	}
	if report.Status != health.StatusOK {
		t.Errorf("status field = %q, want %q", report.Status, health.StatusOK)
	}
	if len(report.Checks) != 0 {
		t.Errorf("liveness reported checks %v; it must probe nothing", report.Checks)
	}
	if report.Build.Service != "receiver" {
		t.Errorf("build.service = %q, want the service name", report.Build.Service)
	}
}

func TestReadinessNamesTheFailedDependency(t *testing.T) {
	handler := newHandler(healthy("clickhouse"), failing("kafka", errors.New("dial: refused")))

	status, report := get(t, handler, health.ReadinessPath)

	if status != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want %d", status, http.StatusServiceUnavailable)
	}
	if report.Checks["kafka"].Status != health.StatusFailed {
		t.Errorf("kafka = %+v, want failed", report.Checks["kafka"])
	}
	if report.Checks["kafka"].Error == "" {
		t.Error("a failed check must say why")
	}
	if report.Checks["clickhouse"].Status != health.StatusOK {
		t.Errorf("clickhouse = %+v, want ok", report.Checks["clickhouse"])
	}
}

func TestReadinessIsGreenWithEveryDependencyUp(t *testing.T) {
	handler := newHandler(healthy("kafka"))

	status, report := get(t, handler, health.ReadinessPath)

	if status != http.StatusOK || report.Status != health.StatusOK {
		t.Errorf("status = %d/%q, want 200/ok", status, report.Status)
	}
}

func TestReadinessRedactsCredentialsFromProbeErrors(t *testing.T) {
	const secret = "hunter2-do-not-log-this"
	err := errors.New("dial postgresql://nexovue:" + secret + "@db:5432/nexovue: refused")
	handler := newHandler(failing("postgres", err))

	_, report := get(t, handler, health.ReadinessPath)

	if got := report.Checks["postgres"].Error; got == "" || strings.Contains(got, secret) {
		t.Fatalf("probe error leaked a credential: %q", got)
	}
}

func TestASlowProbeFailsOnItsOwnDeadline(t *testing.T) {
	slow := health.Check{
		Name:    "kafka",
		Timeout: 10 * time.Millisecond,
		Probe: func(ctx context.Context) error {
			<-ctx.Done()
			return ctx.Err()
		},
	}
	handler := newHandler(slow, healthy("redis"))

	status, report := get(t, handler, health.ReadinessPath)

	if status != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want %d", status, http.StatusServiceUnavailable)
	}
	if report.Checks["redis"].Status != health.StatusOK {
		t.Error("a slow dependency must not take a healthy one down with it")
	}
}

func TestAProbeThatIgnoresItsContextStillFailsOnTime(t *testing.T) {
	// franz-go's Ping keeps retrying past a cancelled context; readiness must
	// answer anyway.
	stuck := health.Check{
		Name:    "kafka",
		Timeout: 10 * time.Millisecond,
		Probe: func(context.Context) error {
			time.Sleep(2 * time.Second)
			return nil
		},
	}
	handler := newHandler(stuck)

	done := make(chan struct{})
	var status int
	var report health.Report
	go func() {
		defer close(done)
		status, report = get(t, handler, health.ReadinessPath)
	}()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("readiness hung on a probe that ignores its deadline")
	}
	if status != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want %d", status, http.StatusServiceUnavailable)
	}
	if report.Checks["kafka"].Status != health.StatusFailed {
		t.Errorf("kafka = %+v, want failed", report.Checks["kafka"])
	}
}
