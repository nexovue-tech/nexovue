package metricwriting

import (
	"errors"
	"testing"
	"time"

	metricsvc "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

const (
	testTenant  = "tenant-a"
	testProject = "project-a"
	testIngest  = "ingest-a"
)

var testMarks = stamps{receivedAt: testTime.Add(time.Second), retentionDays: 395}

// A gauge is a sampled value: no temporality, no monotonicity, and the resource
// and scope facts carried down onto every point.
func TestAGaugePointBecomesAGaugeRow(t *testing.T) {
	out := flattenOne(t, metricOf(t, "queue.depth", &metricspb.Metric_Gauge{
		Gauge: &metricspb.Gauge{DataPoints: []*metricspb.NumberDataPoint{
			numberPointOf(t, &metricspb.NumberDataPoint_AsInt{AsInt: 42}),
		}},
	}))

	if len(out.gauge) != 1 || out.gauge[0].Value != 42 {
		t.Fatalf("gauge rows = %+v, want one row valued 42", out.gauge)
	}
	row := out.gauge[0]
	if row.ServiceName != "checkout" || row.ScopeName != "test-scope" ||
		row.ScopeVersion != "1.0.0" || row.MetricName != "queue.depth" {
		t.Errorf("row identity = %+v, want the resource, scope and metric facts", row.point)
	}
	if row.RetentionDays != testMarks.retentionDays || !row.ReceivedAt.Equal(testMarks.receivedAt) {
		t.Errorf("retention_days = %d, received_at = %s; both must come from us, not the payload",
			row.RetentionDays, row.ReceivedAt)
	}
	if row.TenantID != testTenant || row.ProjectID != testProject {
		t.Errorf("tenancy = %q/%q, want %q/%q",
			row.TenantID, row.ProjectID, testTenant, testProject)
	}
}

// Temporality and monotonicity are the two facts that make a sum readable, and
// neither can be recovered later. They are written exactly as sent — a
// cumulative counter stays cumulative rather than being converted here.
func TestASumKeepsTheTemporalityItWasSentWith(t *testing.T) {
	out := flattenOne(t, metricOf(t, "http.requests", &metricspb.Metric_Sum{
		Sum: &metricspb.Sum{
			AggregationTemporality: metricspb.AggregationTemporality_AGGREGATION_TEMPORALITY_CUMULATIVE,
			IsMonotonic:            true,
			DataPoints: []*metricspb.NumberDataPoint{
				numberPointOf(t, &metricspb.NumberDataPoint_AsDouble{AsDouble: 7.5}),
			},
		},
	}))

	if len(out.sum) != 1 {
		t.Fatalf("sum rows = %d, want 1", len(out.sum))
	}
	row := out.sum[0]
	if row.Temporality != "CUMULATIVE" {
		t.Errorf("aggregation_temporality = %q, want CUMULATIVE", row.Temporality)
	}
	if !row.Monotonic {
		t.Error("is_monotonic = false, want the value the SDK sent")
	}
	if row.Value != 7.5 {
		t.Errorf("value = %v, want 7.5", row.Value)
	}
	if !row.StartTimestamp.Equal(testTime) {
		t.Errorf("start_timestamp = %s, want %s; a cumulative counter is unreadable without it",
			row.StartTimestamp, testTime)
	}
}

// An SDK that never said which temporality it uses has said something, and
// guessing CUMULATIVE here would read later as a claim it never made.
func TestAnUnspecifiedTemporalityIsStoredAsUnspecified(t *testing.T) {
	out := flattenOne(t, metricOf(t, "odd.counter", &metricspb.Metric_Sum{
		Sum: &metricspb.Sum{DataPoints: []*metricspb.NumberDataPoint{
			numberPointOf(t, &metricspb.NumberDataPoint_AsInt{AsInt: 1}),
		}},
	}))

	if len(out.sum) != 1 || out.sum[0].Temporality != "UNSPECIFIED" {
		t.Fatalf("temporality = %+v, want UNSPECIFIED", out.sum)
	}
}

// bucket_counts has one more element than explicit_bounds, and both must reach
// the row untouched or the histogram cannot be read back.
func TestAHistogramKeepsItsBucketsAndItsOptionals(t *testing.T) {
	sum, minimum, maximum := 30.0, 1.0, 9.0
	out := flattenOne(t, metricOf(t, "http.duration", &metricspb.Metric_Histogram{
		Histogram: &metricspb.Histogram{
			AggregationTemporality: metricspb.AggregationTemporality_AGGREGATION_TEMPORALITY_DELTA,
			DataPoints: []*metricspb.HistogramDataPoint{{
				TimeUnixNano:      uint64(testTime.UnixNano()),
				StartTimeUnixNano: uint64(testTime.UnixNano()),
				Count:             6,
				Sum:               &sum,
				BucketCounts:      []uint64{1, 2, 3},
				ExplicitBounds:    []float64{5, 10},
				Min:               &minimum,
				Max:               &maximum,
			}},
		},
	}))

	if len(out.histogram) != 1 {
		t.Fatalf("histogram rows = %d, want 1", len(out.histogram))
	}
	row := out.histogram[0]
	if len(row.BucketCounts) != len(row.ExplicitBounds)+1 {
		t.Errorf("bucket_counts = %v against bounds %v; OTLP requires one more count than bound",
			row.BucketCounts, row.ExplicitBounds)
	}
	if row.Count != 6 || row.Sum == nil || *row.Sum != 30 {
		t.Errorf("count/sum = %d/%v, want 6/30", row.Count, row.Sum)
	}
	if row.Min == nil || *row.Min != 1 || row.Max == nil || *row.Max != 9 {
		t.Errorf("min/max = %v/%v, want 1/9", row.Min, row.Max)
	}
	if row.Temporality != "DELTA" {
		t.Errorf("aggregation_temporality = %q, want DELTA", row.Temporality)
	}
}

// OTLP marks sum, min and max optional. Absent must stay absent all the way to
// the row: a histogram with no sum and one summing to zero are different facts.
func TestAnAbsentHistogramOptionalStaysAbsent(t *testing.T) {
	out := flattenOne(t, metricOf(t, "http.duration", &metricspb.Metric_Histogram{
		Histogram: &metricspb.Histogram{
			AggregationTemporality: metricspb.AggregationTemporality_AGGREGATION_TEMPORALITY_DELTA,
			DataPoints: []*metricspb.HistogramDataPoint{{
				TimeUnixNano: uint64(testTime.UnixNano()),
				Count:        3,
				BucketCounts: []uint64{3},
			}},
		},
	}))

	if len(out.histogram) != 1 {
		t.Fatalf("histogram rows = %d, want 1", len(out.histogram))
	}
	row := out.histogram[0]
	if row.Sum != nil {
		t.Errorf("sum = %v, want absent", *row.Sum)
	}
	if row.Min != nil || row.Max != nil {
		t.Errorf("min/max = %v/%v, want both absent", row.Min, row.Max)
	}
}

// An exponential histogram describes its buckets with a scale and two offsets
// rather than a bound list. Every one of those is needed to reconstruct it.
func TestAnExponentialHistogramKeepsItsScaleAndOffsets(t *testing.T) {
	out := flattenOne(t, metricOf(t, "rpc.duration", &metricspb.Metric_ExponentialHistogram{
		ExponentialHistogram: &metricspb.ExponentialHistogram{
			AggregationTemporality: metricspb.AggregationTemporality_AGGREGATION_TEMPORALITY_DELTA,
			DataPoints: []*metricspb.ExponentialHistogramDataPoint{{
				TimeUnixNano: uint64(testTime.UnixNano()),
				Count:        10,
				Scale:        -3,
				ZeroCount:    2,
				Positive: &metricspb.ExponentialHistogramDataPoint_Buckets{
					Offset: 4, BucketCounts: []uint64{5, 1},
				},
				Negative: &metricspb.ExponentialHistogramDataPoint_Buckets{
					Offset: -2, BucketCounts: []uint64{2},
				},
			}},
		},
	}))

	if len(out.expHistogram) != 1 {
		t.Fatalf("exponential histogram rows = %d, want 1", len(out.expHistogram))
	}
	row := out.expHistogram[0]
	if row.Scale != -3 || row.ZeroCount != 2 {
		t.Errorf("scale/zero_count = %d/%d, want -3/2", row.Scale, row.ZeroCount)
	}
	if row.PositiveOffset != 4 || len(row.PositiveBucketCounts) != 2 {
		t.Errorf("positive = offset %d, counts %v", row.PositiveOffset, row.PositiveBucketCounts)
	}
	if row.NegativeOffset != -2 || len(row.NegativeBucketCounts) != 1 {
		t.Errorf("negative = offset %d, counts %v", row.NegativeOffset, row.NegativeBucketCounts)
	}
}

// A summary's quantiles are pairs; the table stores them as two parallel arrays
// that must stay the same length.
func TestASummarySplitsItsQuantilesIntoParallelArrays(t *testing.T) {
	out := flattenOne(t, metricOf(t, "legacy.latency", &metricspb.Metric_Summary{
		Summary: &metricspb.Summary{DataPoints: []*metricspb.SummaryDataPoint{{
			TimeUnixNano: uint64(testTime.UnixNano()),
			Count:        4,
			Sum:          12,
			QuantileValues: []*metricspb.SummaryDataPoint_ValueAtQuantile{
				{Quantile: 0.5, Value: 2},
				{Quantile: 0.99, Value: 8},
			},
		}}},
	}))

	if len(out.summary) != 1 {
		t.Fatalf("summary rows = %d, want 1", len(out.summary))
	}
	row := out.summary[0]
	if len(row.Quantiles) != len(row.QuantileValues) || len(row.Quantiles) != 2 {
		t.Fatalf("quantiles = %v, values = %v; want two of each",
			row.Quantiles, row.QuantileValues)
	}
	if row.Quantiles[1] != 0.99 || row.QuantileValues[1] != 8 {
		t.Errorf("second quantile = %v at %v, want 8 at 0.99",
			row.QuantileValues[1], row.Quantiles[1])
	}
}

// An exemplar is the only link from a metric point back to a trace, which is
// the product. Its ids are hex at the widths the FixedString columns declare.
func TestExemplarsCarryTheTraceTheyPointAt(t *testing.T) {
	traceID := []byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}
	spanID := []byte{1, 2, 3, 4, 5, 6, 7, 8}

	dataPoint := numberPointOf(t, &metricspb.NumberDataPoint_AsInt{AsInt: 1})
	dataPoint.Exemplars = []*metricspb.Exemplar{{
		TimeUnixNano: uint64(testTime.UnixNano()),
		Value:        &metricspb.Exemplar_AsDouble{AsDouble: 1.25},
		TraceId:      traceID,
		SpanId:       spanID,
	}}

	out := flattenOne(t, metricOf(t, "queue.depth", &metricspb.Metric_Gauge{
		Gauge: &metricspb.Gauge{DataPoints: []*metricspb.NumberDataPoint{dataPoint}},
	}))

	if len(out.gauge) != 1 {
		t.Fatalf("gauge rows = %d, want 1", len(out.gauge))
	}
	got := out.gauge[0].Exemplars
	if len(got.TraceIDs) != 1 || len(got.TraceIDs[0]) != otlpvalue.TraceIDHexLen {
		t.Fatalf("exemplar trace ids = %v, want one of %d bytes", got.TraceIDs, otlpvalue.TraceIDHexLen)
	}
	if string(got.TraceIDs[0]) != "0102030405060708090a0b0c0d0e0f10" {
		t.Errorf("trace id = %q, want it hex-encoded", got.TraceIDs[0])
	}
	if len(got.SpanIDs[0]) != otlpvalue.SpanIDHexLen {
		t.Errorf("span id is %d bytes, want %d", len(got.SpanIDs[0]), otlpvalue.SpanIDHexLen)
	}
	if got.Values[0] != 1.25 {
		t.Errorf("exemplar value = %v, want 1.25", got.Values[0])
	}
}

// NO_RECORDED_VALUE marks a gap in a series — Prometheus's staleness marker.
// The row must still be written: a gap is a fact, and dropping it would leave a
// reader unable to tell a gap from a zero.
func TestANoRecordedValuePointIsStillWritten(t *testing.T) {
	gap := &metricspb.NumberDataPoint{
		TimeUnixNano: uint64(testTime.UnixNano()),
		Flags:        uint32(metricspb.DataPointFlags_DATA_POINT_FLAGS_NO_RECORDED_VALUE_MASK),
	}
	out := flattenOne(t, metricOf(t, "queue.depth", &metricspb.Metric_Gauge{
		Gauge: &metricspb.Gauge{DataPoints: []*metricspb.NumberDataPoint{gap}},
	}))

	if len(out.gauge) != 1 {
		t.Fatalf("gauge rows = %d, want the gap marker written as a row", len(out.gauge))
	}
	if out.gauge[0].Flags != 1 {
		t.Errorf("flags = %d, want the NO_RECORDED_VALUE bit set", out.gauge[0].Flags)
	}
}

// A metric declaring a shape but carrying no points is valid OTLP — an empty
// stream. It must leave no row at all rather than a row of zeros.
func TestAMetricWithNoDataPointsWritesNothing(t *testing.T) {
	for name, metric := range map[string]*metricspb.Metric{
		"gauge":         metricOf(t, "m", &metricspb.Metric_Gauge{Gauge: &metricspb.Gauge{}}),
		"sum":           metricOf(t, "m", &metricspb.Metric_Sum{Sum: &metricspb.Sum{}}),
		"histogram":     metricOf(t, "m", &metricspb.Metric_Histogram{Histogram: &metricspb.Histogram{}}),
		"exp histogram": metricOf(t, "m", &metricspb.Metric_ExponentialHistogram{ExponentialHistogram: &metricspb.ExponentialHistogram{}}),
		"summary":       metricOf(t, "m", &metricspb.Metric_Summary{Summary: &metricspb.Summary{}}),
	} {
		out := flattenOne(t, metric)
		if total := len(out.gauge) + len(out.sum) + len(out.histogram) +
			len(out.expHistogram) + len(out.summary); total != 0 {
			t.Errorf("an empty %s produced %d rows, want none", name, total)
		}
	}
}

// One OTLP request can carry every shape at once, and each must land in its own
// table.
func TestOneRequestCanFillEveryTable(t *testing.T) {
	out := flatten(t, request(
		metricOf(t, "g", &metricspb.Metric_Gauge{Gauge: &metricspb.Gauge{
			DataPoints: []*metricspb.NumberDataPoint{numberPointOf(t, &metricspb.NumberDataPoint_AsInt{AsInt: 1})},
		}}),
		metricOf(t, "s", &metricspb.Metric_Sum{Sum: &metricspb.Sum{
			DataPoints: []*metricspb.NumberDataPoint{numberPointOf(t, &metricspb.NumberDataPoint_AsInt{AsInt: 1})},
		}}),
		metricOf(t, "h", &metricspb.Metric_Histogram{Histogram: &metricspb.Histogram{
			DataPoints: []*metricspb.HistogramDataPoint{{TimeUnixNano: uint64(testTime.UnixNano())}},
		}}),
		metricOf(t, "e", &metricspb.Metric_ExponentialHistogram{
			ExponentialHistogram: &metricspb.ExponentialHistogram{
				DataPoints: []*metricspb.ExponentialHistogramDataPoint{{TimeUnixNano: uint64(testTime.UnixNano())}},
			},
		}),
		metricOf(t, "q", &metricspb.Metric_Summary{Summary: &metricspb.Summary{
			DataPoints: []*metricspb.SummaryDataPoint{{TimeUnixNano: uint64(testTime.UnixNano())}},
		}}),
	))

	for name, count := range map[string]int{
		"gauge": len(out.gauge), "sum": len(out.sum), "histogram": len(out.histogram),
		"exp histogram": len(out.expHistogram), "summary": len(out.summary),
	} {
		if count != 1 {
			t.Errorf("%s rows = %d, want 1", name, count)
		}
	}
}

// A metric whose `data` oneof is unset carries no point shape at all. Writing
// the rest of the message and dropping it would lose customer data with nothing
// to show for it, so the message is rejected and parked — attributed, because a
// parked message skipped masking and holds unmasked customer data.
func TestAMetricWithNoPointShapeRejectsTheMessage(t *testing.T) {
	_, err := flattenRequest(t, request(&metricspb.Metric{Name: "shapeless"}))
	if err == nil {
		t.Fatal("flatten() succeeded; a metric with no point shape must be parked")
	}
	var rejection *consume.Rejection
	if !errors.As(err, &rejection) {
		t.Fatalf("flatten() returned %T, want a *consume.Rejection", err)
	}
	if rejection.TenantID != testTenant || rejection.ProjectID != testProject ||
		rejection.IngestID != testIngest {
		t.Errorf("rejection names %+v; the parked message cannot be attributed", rejection)
	}
}

// A payload that is not an OTLP metrics request at all still has to be parked
// with the tenant the envelope named.
func TestAnUnreadableMetricPayloadIsRejectedWithItsTenant(t *testing.T) {
	// Not valid protobuf at any level: the first tag never terminates.
	message := envelopeBytes(t, []byte{0xff, 0xfe})

	_, err := decodeEnvelope(message)
	if err == nil {
		t.Fatal("decodeEnvelope() succeeded; this message must be parked")
	}
	var rejection *consume.Rejection
	if !errors.As(err, &rejection) {
		t.Fatalf("decodeEnvelope() returned %T, want a *consume.Rejection", err)
	}
	if rejection.TenantID != testTenant || rejection.ProjectID != testProject {
		t.Errorf("rejection names %q/%q, want %q/%q",
			rejection.TenantID, rejection.ProjectID, testTenant, testProject)
	}
}

// A metrics pipeline handed a span envelope is a misconfigured topic, not a
// payload to guess at.
func TestAnEnvelopeForAnotherSignalIsRejected(t *testing.T) {
	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId: testTenant, ProjectId: testProject, IngestId: testIngest,
		Signal: nexovuev1.Signal_SIGNAL_SPANS,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	if _, err := decodeEnvelope(message); err == nil {
		t.Fatal("decodeEnvelope() accepted a span envelope")
	}
}

// --- helpers -------------------------------------------------------------

func flattenOne(t *testing.T, metric *metricspb.Metric) rows {
	t.Helper()
	return flatten(t, request(metric))
}

func flatten(t *testing.T, req *metricsvc.ExportMetricsServiceRequest) rows {
	t.Helper()
	out, err := flattenRequest(t, req)
	if err != nil {
		t.Fatalf("flatten() failed: %v", err)
	}
	return out
}

func flattenRequest(
	t *testing.T,
	req *metricsvc.ExportMetricsServiceRequest,
) (rows, error) {
	t.Helper()
	payload, err := proto.Marshal(req)
	if err != nil {
		t.Fatalf("marshalling the request: %v", err)
	}
	decodedMessage, err := decodeEnvelope(envelopeBytes(t, payload))
	if err != nil {
		t.Fatalf("decodeEnvelope() failed: %v", err)
	}
	return decodedMessage.flatten(testMarks)
}

func envelopeBytes(t *testing.T, payload []byte) []byte {
	t.Helper()
	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:  testTenant,
		ProjectId: testProject,
		IngestId:  testIngest,
		Door:      nexovuev1.Door_DOOR_OTLP,
		Signal:    nexovuev1.Signal_SIGNAL_METRICS,
		Payload:   payload,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	return message
}

func request(metrics ...*metricspb.Metric) *metricsvc.ExportMetricsServiceRequest {
	return &metricsvc.ExportMetricsServiceRequest{
		ResourceMetrics: []*metricspb.ResourceMetrics{{
			Resource: &resourcepb.Resource{Attributes: []*commonpb.KeyValue{{
				Key:   attrServiceName,
				Value: &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: "checkout"}},
			}}},
			ScopeMetrics: []*metricspb.ScopeMetrics{{
				Scope:   &commonpb.InstrumentationScope{Name: "test-scope", Version: "1.0.0"},
				Metrics: metrics,
			}},
		}},
	}
}

// metricOf names a metric carrying one of OTLP's five point shapes. The oneof
// wrapper types satisfy an interface the generated package keeps unexported, so
// a type switch is what lets one helper take any of the five; an unlisted shape
// fails the test rather than producing an empty metric.
func metricOf(t *testing.T, name string, data any) *metricspb.Metric {
	t.Helper()
	metric := &metricspb.Metric{Name: name, Unit: "1", Description: "a test metric"}
	switch shape := data.(type) {
	case *metricspb.Metric_Gauge:
		metric.Data = shape
	case *metricspb.Metric_Sum:
		metric.Data = shape
	case *metricspb.Metric_Histogram:
		metric.Data = shape
	case *metricspb.Metric_ExponentialHistogram:
		metric.Data = shape
	case *metricspb.Metric_Summary:
		metric.Data = shape
	default:
		t.Fatalf("metricOf got %T, which is not an OTLP point shape", data)
	}
	return metric
}

// numberPointOf builds a gauge or sum point around OTLP's double-or-int union,
// which is unexported for the same reason.
func numberPointOf(t *testing.T, value any) *metricspb.NumberDataPoint {
	t.Helper()
	dataPoint := &metricspb.NumberDataPoint{
		TimeUnixNano:      uint64(testTime.UnixNano()),
		StartTimeUnixNano: uint64(testTime.UnixNano()),
	}
	switch typed := value.(type) {
	case *metricspb.NumberDataPoint_AsInt:
		dataPoint.Value = typed
	case *metricspb.NumberDataPoint_AsDouble:
		dataPoint.Value = typed
	default:
		t.Fatalf("numberPointOf got %T, which is not an OTLP number value", value)
	}
	return dataPoint
}
