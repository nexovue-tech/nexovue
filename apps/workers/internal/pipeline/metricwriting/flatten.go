package metricwriting

import (
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"

	metricsvc "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// attrServiceName is the resource attribute behind the promoted service_name
// column, the same one the span rows are grouped by.
const attrServiceName = "service.name"

// temporalityPrefix is stripped from OTLP's enum to leave DELTA or CUMULATIVE,
// the short form ADR 0066 chose and the one derive already writes.
const temporalityPrefix = "AGGREGATION_TEMPORALITY_"

// decoded is one Kafka message unpacked: our envelope and the OTLP request it
// carries, still in protobuf form.
//
// Decoding stops here rather than continuing into rows so the mask stage can
// run on the decoded payload. Masking the payload rather than the rows is what
// guarantees an unmasked value cannot reach the store — the stages after it
// never see the original at all.
type decoded struct {
	envelope *nexovuev1.Envelope
	request  *metricsvc.ExportMetricsServiceRequest
}

// decodeEnvelope unpacks one Kafka message. Every failure is a
// *consume.Rejection, so the caller never has to ask whether it knows who the
// message belonged to.
func decodeEnvelope(message []byte) (decoded, error) {
	envelope, err := consume.DecodeEnvelope(message, nexovuev1.Signal_SIGNAL_METRICS)
	if err != nil {
		return decoded{}, err
	}

	var request metricsvc.ExportMetricsServiceRequest
	if err := proto.Unmarshal(envelope.GetPayload(), &request); err != nil {
		return decoded{}, consume.RejectedBy(envelope, fmt.Errorf("decode OTLP payload: %w", err))
	}
	return decoded{envelope: envelope, request: &request}, nil
}

// stamps are the per-message facts every row of that message shares: our clock
// and the project's retention. They come from the envelope and the project
// config, never from the payload.
type stamps struct {
	receivedAt    time.Time
	retentionDays uint16
}

// flatten turns one decoded request into rows, split by the table each point
// belongs in.
//
// A metric whose `data` oneof is unset carries no point shape at all, and a
// shape OTLP adds later has no table here. Either is a message we cannot store
// faithfully, so the whole message is rejected and parked rather than written
// in part: dropping the odd metric would lose customer data silently, and
// writing the rest would leave a hole nothing records.
func (d decoded) flatten(marks stamps) (rows, error) {
	var out rows
	for _, resourceMetrics := range d.request.GetResourceMetrics() {
		resourceAttrs := resourceMetrics.GetResource().GetAttributes()
		shared := point{
			ReceivedAt:    marks.receivedAt,
			RetentionDays: marks.retentionDays,
			TenantID:      d.envelope.GetTenantId(),
			ProjectID:     d.envelope.GetProjectId(),
			ServiceName:   otlpvalue.StringOrEmpty(resourceAttrs, attrServiceName),
			ResourceAttrs: otlpvalue.AttributesJSON(resourceAttrs),
		}
		for _, scopeMetrics := range resourceMetrics.GetScopeMetrics() {
			scoped := shared
			scoped.ScopeName = scopeMetrics.GetScope().GetName()
			scoped.ScopeVersion = scopeMetrics.GetScope().GetVersion()
			if err := appendMetrics(&out, scoped, scopeMetrics.GetMetrics()); err != nil {
				return rows{}, consume.RejectedBy(d.envelope, err)
			}
		}
	}
	return out, nil
}

func appendMetrics(out *rows, scoped point, metrics []*metricspb.Metric) error {
	for _, metric := range metrics {
		if metric == nil {
			continue
		}
		described := scoped
		described.MetricName = metric.GetName()
		described.MetricUnit = metric.GetUnit()
		described.MetricDescription = metric.GetDescription()

		if err := appendMetric(out, described, metric); err != nil {
			return err
		}
	}
	return nil
}

func appendMetric(out *rows, described point, metric *metricspb.Metric) error {
	switch data := metric.GetData().(type) {
	case *metricspb.Metric_Gauge:
		appendGauge(out, described, data.Gauge)
	case *metricspb.Metric_Sum:
		appendSum(out, described, data.Sum)
	case *metricspb.Metric_Histogram:
		appendHistogram(out, described, data.Histogram)
	case *metricspb.Metric_ExponentialHistogram:
		appendExpHistogram(out, described, data.ExponentialHistogram)
	case *metricspb.Metric_Summary:
		appendSummary(out, described, data.Summary)
	default:
		return fmt.Errorf("metric %q carries no point shape this pipeline can store (%T)",
			metric.GetName(), metric.GetData())
	}
	return nil
}

func appendGauge(out *rows, described point, gauge *metricspb.Gauge) {
	for _, dataPoint := range gauge.GetDataPoints() {
		if dataPoint == nil {
			continue
		}
		out.gauge = append(out.gauge, gaugeRow{
			point: numberPoint(described, dataPoint),
			Value: numberValue(dataPoint),
		})
	}
}

func appendSum(out *rows, described point, sum *metricspb.Sum) {
	temporality := temporalityName(sum.GetAggregationTemporality())
	for _, dataPoint := range sum.GetDataPoints() {
		if dataPoint == nil {
			continue
		}
		out.sum = append(out.sum, sumRow{
			point: numberPoint(described, dataPoint),
			Value: numberValue(dataPoint),
			// Written exactly as received. Converting cumulative to delta here
			// would need counter-reset detection, and a guess at that is baked
			// into stored data forever (ADR 0066).
			Temporality: temporality,
			Monotonic:   sum.GetIsMonotonic(),
		})
	}
}

func appendHistogram(out *rows, described point, histogram *metricspb.Histogram) {
	temporality := temporalityName(histogram.GetAggregationTemporality())
	for _, dataPoint := range histogram.GetDataPoints() {
		if dataPoint == nil {
			continue
		}
		measured := described
		measured.Timestamp = unixNanoTime(dataPoint.GetTimeUnixNano())
		measured.StartTimestamp = unixNanoTime(dataPoint.GetStartTimeUnixNano())
		measured.Attrs = otlpvalue.AttributesJSON(dataPoint.GetAttributes())
		measured.Flags = dataPoint.GetFlags()
		measured.Exemplars = flattenExemplars(dataPoint.GetExemplars())

		out.histogram = append(out.histogram, histogramRow{
			point:          measured,
			Count:          dataPoint.GetCount(),
			Sum:            dataPoint.Sum,
			BucketCounts:   dataPoint.GetBucketCounts(),
			ExplicitBounds: dataPoint.GetExplicitBounds(),
			Min:            dataPoint.Min,
			Max:            dataPoint.Max,
			Temporality:    temporality,
		})
	}
}

func appendExpHistogram(
	out *rows,
	described point,
	histogram *metricspb.ExponentialHistogram,
) {
	temporality := temporalityName(histogram.GetAggregationTemporality())
	for _, dataPoint := range histogram.GetDataPoints() {
		if dataPoint == nil {
			continue
		}
		measured := described
		measured.Timestamp = unixNanoTime(dataPoint.GetTimeUnixNano())
		measured.StartTimestamp = unixNanoTime(dataPoint.GetStartTimeUnixNano())
		measured.Attrs = otlpvalue.AttributesJSON(dataPoint.GetAttributes())
		measured.Flags = dataPoint.GetFlags()
		measured.Exemplars = flattenExemplars(dataPoint.GetExemplars())

		out.expHistogram = append(out.expHistogram, expHistogramRow{
			point:                measured,
			Count:                dataPoint.GetCount(),
			Sum:                  dataPoint.Sum,
			Scale:                dataPoint.GetScale(),
			ZeroCount:            dataPoint.GetZeroCount(),
			PositiveOffset:       dataPoint.GetPositive().GetOffset(),
			PositiveBucketCounts: dataPoint.GetPositive().GetBucketCounts(),
			NegativeOffset:       dataPoint.GetNegative().GetOffset(),
			NegativeBucketCounts: dataPoint.GetNegative().GetBucketCounts(),
			Min:                  dataPoint.Min,
			Max:                  dataPoint.Max,
			Temporality:          temporality,
		})
	}
}

func appendSummary(out *rows, described point, summary *metricspb.Summary) {
	for _, dataPoint := range summary.GetDataPoints() {
		if dataPoint == nil {
			continue
		}
		measured := described
		measured.Timestamp = unixNanoTime(dataPoint.GetTimeUnixNano())
		measured.StartTimestamp = unixNanoTime(dataPoint.GetStartTimeUnixNano())
		measured.Attrs = otlpvalue.AttributesJSON(dataPoint.GetAttributes())
		measured.Flags = dataPoint.GetFlags()

		quantiles, values := flattenQuantiles(dataPoint.GetQuantileValues())
		out.summary = append(out.summary, summaryRow{
			point:          measured,
			Count:          dataPoint.GetCount(),
			Sum:            dataPoint.GetSum(),
			Quantiles:      quantiles,
			QuantileValues: values,
		})
	}
}

// numberPoint fills the shared columns of a gauge or sum point.
func numberPoint(described point, dataPoint *metricspb.NumberDataPoint) point {
	described.Timestamp = unixNanoTime(dataPoint.GetTimeUnixNano())
	described.StartTimestamp = unixNanoTime(dataPoint.GetStartTimeUnixNano())
	described.Attrs = otlpvalue.AttributesJSON(dataPoint.GetAttributes())
	described.Flags = dataPoint.GetFlags()
	described.Exemplars = flattenExemplars(dataPoint.GetExemplars())
	return described
}

// numberValue reads a point's value out of OTLP's double-or-int union. The
// column is Float64, so an integer widens; above 2^53 that loses precision,
// which is the column's limit rather than a decision made here.
func numberValue(dataPoint *metricspb.NumberDataPoint) float64 {
	switch typed := dataPoint.GetValue().(type) {
	case *metricspb.NumberDataPoint_AsDouble:
		return typed.AsDouble
	case *metricspb.NumberDataPoint_AsInt:
		return float64(typed.AsInt)
	default:
		// No value set at all. That is what NO_RECORDED_VALUE looks like, and
		// the flags column is what tells the reader so.
		return 0
	}
}

func flattenExemplars(source []*metricspb.Exemplar) exemplars {
	if len(source) == 0 {
		return exemplars{}
	}
	flattened := exemplars{
		Timestamps: make([]time.Time, 0, len(source)),
		Values:     make([]float64, 0, len(source)),
		TraceIDs:   make([][]byte, 0, len(source)),
		SpanIDs:    make([][]byte, 0, len(source)),
	}
	for _, exemplar := range source {
		if exemplar == nil {
			continue
		}
		flattened.Timestamps = append(flattened.Timestamps,
			unixNanoTime(exemplar.GetTimeUnixNano()))
		flattened.Values = append(flattened.Values, exemplarValue(exemplar))
		flattened.TraceIDs = append(flattened.TraceIDs,
			otlpvalue.HexID(exemplar.GetTraceId(), otlpvalue.TraceIDHexLen))
		flattened.SpanIDs = append(flattened.SpanIDs,
			otlpvalue.HexID(exemplar.GetSpanId(), otlpvalue.SpanIDHexLen))
	}
	return flattened
}

func exemplarValue(exemplar *metricspb.Exemplar) float64 {
	switch typed := exemplar.GetValue().(type) {
	case *metricspb.Exemplar_AsDouble:
		return typed.AsDouble
	case *metricspb.Exemplar_AsInt:
		return float64(typed.AsInt)
	default:
		return 0
	}
}

// flattenQuantiles splits OTLP's quantile pairs into the two parallel arrays
// the Nested column stores, keeping them the same length by construction.
func flattenQuantiles(
	source []*metricspb.SummaryDataPoint_ValueAtQuantile,
) (quantiles, values []float64) {
	if len(source) == 0 {
		return nil, nil
	}
	quantiles = make([]float64, 0, len(source))
	values = make([]float64, 0, len(source))
	for _, pair := range source {
		if pair == nil {
			continue
		}
		quantiles = append(quantiles, pair.GetQuantile())
		values = append(values, pair.GetValue())
	}
	return quantiles, values
}

// temporalityName is OTLP's enum in the short form the column holds. An
// unspecified temporality stays UNSPECIFIED rather than being guessed at: an
// SDK that did not say is a fact about the SDK, and CUMULATIVE assumed here
// would read as a claim the sender never made.
func temporalityName(temporality metricspb.AggregationTemporality) string {
	return strings.TrimPrefix(temporality.String(), temporalityPrefix)
}

// unixNanoTime converts an OTLP timestamp to a Go time. OTLP counts nanoseconds
// unsigned and Go counts them signed, so a value past the signed range is not a
// real instant: it becomes the epoch rather than wrapping into the distant past.
func unixNanoTime(unixNano uint64) time.Time {
	if unixNano > math.MaxInt64 {
		return time.Unix(0, 0).UTC()
	}
	return time.Unix(0, int64(unixNano)).UTC()
}
