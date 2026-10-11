package health

import (
	"context"

	"github.com/prometheus/client_golang/prometheus"
)

var checkUpDesc = prometheus.NewDesc("nexovue_worker_check_up",
	"Whether each readiness check passes: every pipeline, meter and dependency.",
	[]string{"check"}, nil)

// checkCollector reports a registry's checks on every scrape, so a monitor that
// reads only /metrics sees a stopped pipeline as plainly as /readyz shows it.
type checkCollector struct{ registry *Registry }

// Collector returns r's checks as a Prometheus collector. Check names are fixed
// at registration, so they are bounded label values.
func (r *Registry) Collector() prometheus.Collector { return checkCollector{registry: r} }

func (c checkCollector) Describe(descs chan<- *prometheus.Desc) { descs <- checkUpDesc }

func (c checkCollector) Collect(metrics chan<- prometheus.Metric) {
	// Each check enforces its own timeout; the registry runs them in parallel.
	for name, result := range c.registry.Run(context.Background()) {
		up := 0.0
		if result.Status == StatusOK {
			up = 1
		}
		metrics <- prometheus.MustNewConstMetric(checkUpDesc, prometheus.GaugeValue, up, name)
	}
}
