package mask_test

import (
	"testing"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/mask"
)

func stringAttr(key, value string) *commonpb.KeyValue {
	return &commonpb.KeyValue{
		Key:   key,
		Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: value}},
	}
}

func attrValue(attrs []*commonpb.KeyValue, key string) string {
	for _, attr := range attrs {
		if attr.GetKey() == key {
			return attr.GetValue().GetStringValue()
		}
	}
	return ""
}

// numberPoint is the shape shared by gauge and sum data points.
func numberPoint(attrs ...*commonpb.KeyValue) *metricspb.NumberDataPoint {
	return &metricspb.NumberDataPoint{Attributes: attrs}
}

// TestSecretsInMetricAttributesDoNotSurviveMasking covers every point shape,
// because a rule that reaches four of five leaves one door open.
func TestSecretsInMetricAttributesDoNotSurviveMasking(t *testing.T) {
	secret := "super-secret-value"

	shapes := map[string]*metricspb.Metric{
		"gauge": {Data: &metricspb.Metric_Gauge{Gauge: &metricspb.Gauge{
			DataPoints: []*metricspb.NumberDataPoint{numberPoint(stringAttr("api_key", secret))},
		}}},
		"sum": {Data: &metricspb.Metric_Sum{Sum: &metricspb.Sum{
			DataPoints: []*metricspb.NumberDataPoint{numberPoint(stringAttr("api_key", secret))},
		}}},
		"histogram": {Data: &metricspb.Metric_Histogram{Histogram: &metricspb.Histogram{
			DataPoints: []*metricspb.HistogramDataPoint{{Attributes: []*commonpb.KeyValue{
				stringAttr("api_key", secret),
			}}},
		}}},
		"exponential histogram": {Data: &metricspb.Metric_ExponentialHistogram{
			ExponentialHistogram: &metricspb.ExponentialHistogram{
				DataPoints: []*metricspb.ExponentialHistogramDataPoint{{Attributes: []*commonpb.KeyValue{
					stringAttr("api_key", secret),
				}}},
			}},
		},
		"summary": {Data: &metricspb.Metric_Summary{Summary: &metricspb.Summary{
			DataPoints: []*metricspb.SummaryDataPoint{{Attributes: []*commonpb.KeyValue{
				stringAttr("api_key", secret),
			}}},
		}}},
	}

	for name, metric := range shapes {
		t.Run(name, func(t *testing.T) {
			masker := mask.New(mask.StaticRules(mask.Defaults()))
			batches := []*metricspb.ResourceMetrics{{
				ScopeMetrics: []*metricspb.ScopeMetrics{{Metrics: []*metricspb.Metric{metric}}},
			}}

			if masked := masker.MaskResourceMetrics("project", batches); masked != 1 {
				t.Fatalf("masked = %d, want 1", masked)
			}
			got := pointAttributes(t, metric)
			if value := attrValue(got, "api_key"); value == secret {
				t.Errorf("the secret survived masking: %q", value)
			}
		})
	}
}

// TestMaskingMetricsReachesTheResourceAndExemplars proves the two carriers that
// are not a data point's own attributes.
func TestMaskingMetricsReachesTheResourceAndExemplars(t *testing.T) {
	masker := mask.New(mask.StaticRules(mask.Defaults()))
	batches := []*metricspb.ResourceMetrics{{
		Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{
			stringAttr("password", "hunter2"),
		}},
		ScopeMetrics: []*metricspb.ScopeMetrics{{Metrics: []*metricspb.Metric{
			{Data: &metricspb.Metric_Sum{Sum: &metricspb.Sum{
				DataPoints: []*metricspb.NumberDataPoint{{
					Exemplars: []*metricspb.Exemplar{{
						FilteredAttributes: []*commonpb.KeyValue{stringAttr("token", "abc123")},
					}},
				}},
			}}},
		}}},
	}}

	if masked := masker.MaskResourceMetrics("project", batches); masked != 2 {
		t.Fatalf("masked = %d, want 2 (resource and exemplar)", masked)
	}
}

// TestMaskingMetricsToleratesMissingParts — a nil anywhere in the tree must not
// panic the pipeline that calls this on every message.
func TestMaskingMetricsToleratesMissingParts(t *testing.T) {
	masker := mask.New(mask.StaticRules(mask.Defaults()))
	batches := []*metricspb.ResourceMetrics{
		nil,
		{ScopeMetrics: []*metricspb.ScopeMetrics{nil}},
		{ScopeMetrics: []*metricspb.ScopeMetrics{{Metrics: []*metricspb.Metric{nil}}}},
		{ScopeMetrics: []*metricspb.ScopeMetrics{{Metrics: []*metricspb.Metric{{}}}}},
	}
	if masked := masker.MaskResourceMetrics("project", batches); masked != 0 {
		t.Errorf("masked = %d, want 0", masked)
	}
}

func pointAttributes(t *testing.T, metric *metricspb.Metric) []*commonpb.KeyValue {
	t.Helper()
	switch data := metric.GetData().(type) {
	case *metricspb.Metric_Gauge:
		return data.Gauge.GetDataPoints()[0].GetAttributes()
	case *metricspb.Metric_Sum:
		return data.Sum.GetDataPoints()[0].GetAttributes()
	case *metricspb.Metric_Histogram:
		return data.Histogram.GetDataPoints()[0].GetAttributes()
	case *metricspb.Metric_ExponentialHistogram:
		return data.ExponentialHistogram.GetDataPoints()[0].GetAttributes()
	case *metricspb.Metric_Summary:
		return data.Summary.GetDataPoints()[0].GetAttributes()
	}
	t.Fatalf("unhandled metric shape %T", metric.GetData())
	return nil
}
