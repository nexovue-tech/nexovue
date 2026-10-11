package health

import (
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// MetricsPath is where the Prometheus text format is served (ADR-021). On the
// admin listener, which no edge routes: it names topics, groups and reasons.
const MetricsPath = "/metrics"

// WithMetrics adds MetricsPath beside the probes, serving what gatherer holds.
func WithMetrics(handler http.Handler, gatherer prometheus.Gatherer) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/", handler)
	mux.Handle("GET "+MetricsPath, promhttp.HandlerFor(gatherer, promhttp.HandlerOpts{
		// A collector that fails is reported in the scrape, and the rest still served.
		ErrorHandling: promhttp.ContinueOnError,
	}))
	return mux
}
