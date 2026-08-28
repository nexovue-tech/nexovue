package mask

import (
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
)

// MaskResourceMetrics masks every value the project's rules cover across a
// metrics payload, and returns how many it replaced.
//
// Like the span and log paths it rewrites the decoded payload in place, so the
// rows the metric writer builds afterwards can only be built from masked
// values. A metric carries far less caller-controlled text than a log, but it
// carries some: a data point's attributes are the series' dimensions, and a
// customer labelling one `api_key` is exactly the case the key rules exist for.
func (m *Masker) MaskResourceMetrics(projectID string, batches []*metricspb.ResourceMetrics) int {
	rules := m.rules.MaskingRules(projectID)
	masked := 0
	for _, batch := range batches {
		if batch == nil {
			continue
		}
		masked += maskAttributes(rules, batch.GetResource().GetAttributes())
		for _, scope := range batch.GetScopeMetrics() {
			for _, metric := range scope.GetMetrics() {
				masked += maskMetric(rules, metric)
			}
		}
	}
	return masked
}

// maskMetric reaches every data point of whichever shape this metric is.
//
// The switch is exhaustive over OTLP's five point types rather than defaulting,
// so a sixth shape added upstream fails to compile here instead of silently
// carrying unmasked attributes to the store.
func maskMetric(rules Rules, metric *metricspb.Metric) int {
	if metric == nil {
		return 0
	}
	masked := 0
	switch data := metric.GetData().(type) {
	case *metricspb.Metric_Gauge:
		for _, point := range data.Gauge.GetDataPoints() {
			masked += maskAttributes(rules, point.GetAttributes())
			masked += maskExemplars(rules, point.GetExemplars())
		}
	case *metricspb.Metric_Sum:
		for _, point := range data.Sum.GetDataPoints() {
			masked += maskAttributes(rules, point.GetAttributes())
			masked += maskExemplars(rules, point.GetExemplars())
		}
	case *metricspb.Metric_Histogram:
		for _, point := range data.Histogram.GetDataPoints() {
			masked += maskAttributes(rules, point.GetAttributes())
			masked += maskExemplars(rules, point.GetExemplars())
		}
	case *metricspb.Metric_ExponentialHistogram:
		for _, point := range data.ExponentialHistogram.GetDataPoints() {
			masked += maskAttributes(rules, point.GetAttributes())
			masked += maskExemplars(rules, point.GetExemplars())
		}
	case *metricspb.Metric_Summary:
		// Summaries carry no exemplars in OTLP.
		for _, point := range data.Summary.GetDataPoints() {
			masked += maskAttributes(rules, point.GetAttributes())
		}
	}
	return masked
}

// maskExemplars covers the attributes an exemplar filtered out of its series.
//
// ADR 0066 drops FilteredAttributes on the way to storage, so these values are
// discarded moments later — but they are masked anyway, because "it is dropped
// downstream" is a reason that stops being true the day someone stores it.
func maskExemplars(rules Rules, exemplars []*metricspb.Exemplar) int {
	masked := 0
	for _, exemplar := range exemplars {
		masked += maskAttributes(rules, exemplar.GetFilteredAttributes())
	}
	return masked
}
