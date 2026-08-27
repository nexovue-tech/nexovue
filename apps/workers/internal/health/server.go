package health

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/buildinfo"
)

// The probe paths. Fixed, because every deployment manifest hardcodes them.
const (
	LivenessPath  = "/healthz"
	ReadinessPath = "/readyz"
)

// Timeouts for the admin listener. Generous on write because readiness waits on
// its probes; strict on read because a probe request has no body.
const (
	readHeaderTimeout = 3 * time.Second
	writeTimeout      = 10 * time.Second
	idleTimeout       = 60 * time.Second
)

// Handler serves the two probe endpoints.
//
// `liveness` holds only checks internal to this process — is the work still
// happening — because a failing liveness probe means "restart me", and restarting
// on a dependency outage turns one broken store into every worker crash-looping.
// `readiness` holds those same checks plus every external dependency.
//
// A worker serves no traffic, so its liveness probe cannot be "the process
// answers HTTP": that stayed true for nine minutes once while the pipeline was
// dead and ingest was silently lost. Internal work checks are what close that.
func Handler(info buildinfo.Info, liveness, readiness *Registry) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("GET "+LivenessPath, livenessHandler(info, liveness))
	mux.Handle("GET "+ReadinessPath, readinessHandler(info, readiness))
	return mux
}

// NewServer returns the admin listener carrying the probe endpoints. It is a
// separate listener from the data port so probes still answer when that port
// is saturated.
func NewServer(addr string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: readHeaderTimeout,
		WriteTimeout:      writeTimeout,
		IdleTimeout:       idleTimeout,
	}
}

// livenessHandler always succeeds. Checking a dependency here would turn one
// database blip into a restart of every replica of every service.
func livenessHandler(info buildinfo.Info, registry *Registry) http.Handler {
	if registry == nil {
		return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			writeReport(w, http.StatusOK, Report{Status: StatusOK, Build: info})
		})
	}
	return readinessHandler(info, registry)
}

func readinessHandler(info buildinfo.Info, registry *Registry) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		results := registry.Run(r.Context())
		report := Report{Status: overallStatus(results), Build: info, Checks: results}

		status := http.StatusOK
		if report.Status != StatusOK {
			status = http.StatusServiceUnavailable
		}
		writeReport(w, status, report)
	})
}

func writeReport(w http.ResponseWriter, status int, report Report) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(report)
}
