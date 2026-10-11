package otlp

import "github.com/prometheus/client_golang/prometheus"

// What a monitor reads from the doors (ADR-021): the totals beside the summary
// line's windows. Every label comes from units and the reason tables, which are
// fixed at construction, so no request can add a series.
var (
	requestsDesc = prometheus.NewDesc("nexovue_receiver_requests_total",
		"Export requests the doors answered, by outcome.", []string{"outcome"}, nil)
	itemsDesc = prometheus.NewDesc("nexovue_receiver_items_total",
		"Telemetry the doors accepted or refused, in each signal's own unit.",
		[]string{"signal", "outcome"}, nil)
	eventsDesc = prometheus.NewDesc("nexovue_receiver_events_total",
		"Refusals and degradations, by reason.", []string{"reason"}, nil)
)

const (
	outcomeAccepted = "accepted"
	outcomeRefused  = "refused"
)

// Describe implements prometheus.Collector.
func (t *Traffic) Describe(descs chan<- *prometheus.Desc) {
	descs <- requestsDesc
	descs <- itemsDesc
	descs <- eventsDesc
}

// Collect implements prometheus.Collector.
func (t *Traffic) Collect(metrics chan<- prometheus.Metric) {
	counter := func(desc *prometheus.Desc, value int64, labels ...string) {
		metrics <- prometheus.MustNewConstMetric(desc, prometheus.CounterValue, float64(value), labels...)
	}
	counter(requestsDesc, t.acceptedRequestsTotal.Load(), outcomeAccepted)
	counter(requestsDesc, t.refusedRequestsTotal.Load(), outcomeRefused)
	for unit, counted := range t.items {
		counter(itemsDesc, counted.acceptedTotal.Load(), string(unit), outcomeAccepted)
		counter(itemsDesc, counted.refusedTotal.Load(), string(unit), outcomeRefused)
	}
	for reason, counted := range t.tallies {
		counter(eventsDesc, counted.total.Load(), reason)
	}
}
