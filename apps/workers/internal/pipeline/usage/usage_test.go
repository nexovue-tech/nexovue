package usage

import (
	"errors"
	"testing"
	"time"

	logsvc "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricsvc "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// The tenancy every test message carries. Real ids are UUIDv7; these only have
// to be distinguishable, because the join to `projects` that resolves them is
// in Postgres and is proved against the running stack, not here.
const (
	testOrg     = "org-1"
	testProject = "project-1"
)

var testReceivedAt = time.Date(2026, 8, 30, 10, 30, 0, 0, time.UTC)

// spanPayload is an OTLP trace export carrying spans spread over two scopes in
// two resources, so a counter that only reads the first of either fails.
func spanPayload(t *testing.T, spans int) []byte {
	t.Helper()

	request := &tracesvc.ExportTraceServiceRequest{}
	for i := range spans {
		if i%2 == 0 || len(request.ResourceSpans) == 0 {
			request.ResourceSpans = append(request.ResourceSpans, &tracepb.ResourceSpans{
				ScopeSpans: []*tracepb.ScopeSpans{{}},
			})
		}
		last := request.ResourceSpans[len(request.ResourceSpans)-1]
		last.ScopeSpans[0].Spans = append(last.ScopeSpans[0].Spans, &tracepb.Span{Name: "GET /checkout"})
	}
	return marshal(t, request)
}

func logPayload(t *testing.T, records int) []byte {
	t.Helper()

	scope := &logspb.ScopeLogs{}
	for range records {
		scope.LogRecords = append(scope.LogRecords, &logspb.LogRecord{})
	}
	return marshal(t, &logsvc.ExportLogsServiceRequest{
		ResourceLogs: []*logspb.ResourceLogs{{ScopeLogs: []*logspb.ScopeLogs{scope}}},
	})
}

// metricPayload carries one metric of every OTLP shape, with a different number
// of points each, so a counter that misses a shape or double-counts one fails.
func metricPayload(t *testing.T) []byte {
	t.Helper()

	metrics := []*metricspb.Metric{
		{Data: &metricspb.Metric_Gauge{Gauge: &metricspb.Gauge{
			DataPoints: make([]*metricspb.NumberDataPoint, 1),
		}}},
		{Data: &metricspb.Metric_Sum{Sum: &metricspb.Sum{
			DataPoints: make([]*metricspb.NumberDataPoint, 2),
		}}},
		{Data: &metricspb.Metric_Histogram{Histogram: &metricspb.Histogram{
			DataPoints: make([]*metricspb.HistogramDataPoint, 3),
		}}},
		{Data: &metricspb.Metric_ExponentialHistogram{
			ExponentialHistogram: &metricspb.ExponentialHistogram{
				DataPoints: make([]*metricspb.ExponentialHistogramDataPoint, 4),
			},
		}},
		{Data: &metricspb.Metric_Summary{Summary: &metricspb.Summary{
			DataPoints: make([]*metricspb.SummaryDataPoint, 5),
		}}},
		// No data at all: metric-ingest stores nothing for it, so the meter
		// must charge nothing for it.
		{Name: "empty"},
	}
	return marshal(t, &metricsvc.ExportMetricsServiceRequest{
		ResourceMetrics: []*metricspb.ResourceMetrics{
			{ScopeMetrics: []*metricspb.ScopeMetrics{{Metrics: metrics}}},
		},
	})
}

// envelope wraps a payload the way every door does before the broker.
func envelope(t *testing.T, signal nexovuev1.Signal, payload []byte, receivedAt time.Time) []byte {
	t.Helper()

	return marshal(t, &nexovuev1.Envelope{
		TenantId:           testOrg,
		ProjectId:          testProject,
		Door:               nexovuev1.Door_DOOR_OTLP,
		Signal:             signal,
		ReceivedAtUnixNano: receivedAt.UnixNano(),
		IngestId:           "ingest-1",
		Payload:            payload,
	})
}

func spanMessage(t *testing.T, spans int, receivedAt time.Time) []byte {
	t.Helper()
	return envelope(t, nexovuev1.Signal_SIGNAL_SPANS, spanPayload(t, spans), receivedAt)
}

func marshal(t *testing.T, message proto.Message) []byte {
	t.Helper()

	encoded, err := proto.Marshal(message)
	if err != nil {
		t.Fatalf("marshalling %T: %v", message, err)
	}
	return encoded
}

func TestSpansAreCountedAcrossEveryResourceAndScope(t *testing.T) {
	payload := spanPayload(t, 7)

	counts, err := countSpans(payload)
	if err != nil {
		t.Fatalf("counting spans: %v", err)
	}
	if counts.Spans != 7 {
		t.Errorf("spans = %d, want 7", counts.Spans)
	}
	if counts.Bytes != int64(len(payload)) {
		t.Errorf("bytes = %d, want the payload's %d", counts.Bytes, len(payload))
	}
	if counts.Logs != 0 || counts.MetricPoints != 0 {
		t.Errorf("a trace export fed another signal's counter: %+v", counts)
	}
}

func TestLogRecordsAreCounted(t *testing.T) {
	counts, err := countLogs(logPayload(t, 4))
	if err != nil {
		t.Fatalf("counting logs: %v", err)
	}
	if counts.Logs != 4 {
		t.Errorf("logs = %d, want 4", counts.Logs)
	}
}

func TestEveryMetricShapeIsCountedInDataPoints(t *testing.T) {
	counts, err := countMetricPoints(metricPayload(t))
	if err != nil {
		t.Fatalf("counting metric points: %v", err)
	}
	// 1 gauge + 2 sum + 3 histogram + 4 exponential histogram + 5 summary.
	if counts.MetricPoints != 15 {
		t.Errorf("metric points = %d, want 15", counts.MetricPoints)
	}
}

func TestAMessageOnTheWrongTopicIsNotMetered(t *testing.T) {
	message := envelope(t, nexovuev1.Signal_SIGNAL_LOGS, logPayload(t, 3), testReceivedAt)

	if _, _, err := Spans.meter(message); err == nil {
		t.Fatal("a log envelope was metered as spans; a topic holding another " +
			"signal is a misconfiguration, not a payload to count")
	}
}

func TestAnUndecodableMessageIsRejectedWithItsTenant(t *testing.T) {
	message := envelope(t, nexovuev1.Signal_SIGNAL_SPANS, []byte("not OTLP"), testReceivedAt)

	_, _, err := Spans.meter(message)
	var rejection *consume.Rejection
	if !errors.As(err, &rejection) {
		t.Fatalf("meter() error = %v, want a *consume.Rejection", err)
	}
	if rejection.TenantID != testOrg || rejection.ProjectID != testProject {
		t.Errorf("rejection names %s/%s, want %s/%s; a message nobody can "+
			"attribute is one nobody can chase",
			rejection.TenantID, rejection.ProjectID, testOrg, testProject)
	}
}
