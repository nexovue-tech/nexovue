package usage

import (
	"errors"
	"slices"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	logsvc "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricsvc "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	tracesvc "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
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

// resourceWith is an OTLP resource carrying one service name, or none at all
// when the name is empty — the sender that assembled a resource by hand and
// left `service.name` out.
func resourceWith(service string) *resourcepb.Resource {
	if service == "" {
		return &resourcepb.Resource{}
	}
	return &resourcepb.Resource{Attributes: []*commonpb.KeyValue{{
		Key: attrServiceName,
		Value: &commonpb.AnyValue{
			Value: &commonpb.AnyValue_StringValue{StringValue: service},
		},
	}}}
}

// spansFrom is a trace export with one resource per named service, each
// carrying spansEach spans. One export can carry several services — a
// collector forwarding for a fleet does exactly this.
func spansFrom(t *testing.T, spansEach int, servicesNamed ...string) []byte {
	t.Helper()

	request := &tracesvc.ExportTraceServiceRequest{}
	for _, service := range servicesNamed {
		request.ResourceSpans = append(request.ResourceSpans, &tracepb.ResourceSpans{
			Resource:   resourceWith(service),
			ScopeSpans: []*tracepb.ScopeSpans{{Spans: make([]*tracepb.Span, spansEach)}},
		})
	}
	return marshal(t, request)
}

// logsFrom is the same for logs, so a service that only ever logs is proved to
// be metered as a service.
func logsFrom(t *testing.T, recordsEach int, servicesNamed ...string) []byte {
	t.Helper()

	request := &logsvc.ExportLogsServiceRequest{}
	for _, service := range servicesNamed {
		request.ResourceLogs = append(request.ResourceLogs, &logspb.ResourceLogs{
			Resource: resourceWith(service),
			ScopeLogs: []*logspb.ScopeLogs{
				{LogRecords: make([]*logspb.LogRecord, recordsEach)},
			},
		})
	}
	return marshal(t, request)
}

// metricsFrom is the same for metric points.
func metricsFrom(t *testing.T, pointsEach int, servicesNamed ...string) []byte {
	t.Helper()

	request := &metricsvc.ExportMetricsServiceRequest{}
	for _, service := range servicesNamed {
		request.ResourceMetrics = append(request.ResourceMetrics, &metricspb.ResourceMetrics{
			Resource: resourceWith(service),
			ScopeMetrics: []*metricspb.ScopeMetrics{{Metrics: []*metricspb.Metric{
				{Data: &metricspb.Metric_Gauge{Gauge: &metricspb.Gauge{
					DataPoints: make([]*metricspb.NumberDataPoint, pointsEach),
				}}},
			}}},
		})
	}
	return marshal(t, request)
}

// envelope wraps a payload the way the server door does before the broker.
func envelope(t *testing.T, signal nexovuev1.Signal, payload []byte, receivedAt time.Time) []byte {
	t.Helper()
	return envelopeThrough(t, nexovuev1.Door_DOOR_OTLP, signal, payload, receivedAt)
}

// envelopeThrough wraps a payload as one named door stamps it.
func envelopeThrough(
	t *testing.T,
	door nexovuev1.Door,
	signal nexovuev1.Signal,
	payload []byte,
	receivedAt time.Time,
) []byte {
	t.Helper()

	return marshal(t, &nexovuev1.Envelope{
		TenantId:           testOrg,
		ProjectId:          testProject,
		Door:               door,
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

// --------------------------------------------- which door the bytes came through

// The browser door's bytes are charged to the browser's share of the allowance
// and to nothing else (ADR 0097). Every other door — the server's, the
// scheduler's, and an envelope naming none — is the server's, because a byte
// the browser door cannot spend is what the split protects. The whole count
// still carries every byte, so the usage screen's total does not change.
func TestOnlyTheBrowserDoorsBytesAreBrowserBytes(t *testing.T) {
	payload := spanPayload(t, 3)
	cases := []struct {
		door        nexovuev1.Door
		wantBrowser int64
	}{
		{nexovuev1.Door_DOOR_RUM, int64(len(payload))},
		{nexovuev1.Door_DOOR_OTLP, 0},
		{nexovuev1.Door_DOOR_SYNTHETIC, 0},
		{nexovuev1.Door_DOOR_POLLER, 0},
		{nexovuev1.Door_DOOR_UNSPECIFIED, 0},
	}
	for _, tc := range cases {
		t.Run(tc.door.String(), func(t *testing.T) {
			message := envelopeThrough(t, tc.door, nexovuev1.Signal_SIGNAL_SPANS, payload, testReceivedAt)

			_, metered, err := Spans.meter(message)
			if err != nil {
				t.Fatalf("metering: %v", err)
			}
			if metered.BrowserBytes != tc.wantBrowser {
				t.Errorf("browser bytes = %d, want %d", metered.BrowserBytes, tc.wantBrowser)
			}
			if metered.Bytes != int64(len(payload)) {
				t.Errorf("bytes = %d, want the whole payload's %d: the door changes which "+
					"share is charged, never the total", metered.Bytes, len(payload))
			}
		})
	}
}

// Logs and metrics through the browser door are the browser's too; the share
// is bytes across all three signals, exactly as the allowance is.
func TestEverySignalThroughTheBrowserDoorIsBrowserBytes(t *testing.T) {
	cases := []struct {
		signal  Signal
		payload []byte
	}{
		{Logs, logPayload(t, 2)},
		{Metrics, metricPayload(t)},
	}
	for _, tc := range cases {
		t.Run(tc.signal.Name, func(t *testing.T) {
			message := envelopeThrough(t, nexovuev1.Door_DOOR_RUM, tc.signal.wire, tc.payload, testReceivedAt)

			_, metered, err := tc.signal.meter(message)
			if err != nil {
				t.Fatalf("metering: %v", err)
			}
			if metered.BrowserBytes != int64(len(tc.payload)) {
				t.Errorf("browser bytes = %d, want %d", metered.BrowserBytes, len(tc.payload))
			}
		})
	}
}

// The flush carries browser bytes in their own column, beside the whole count.
// Asserted at the column: a meter that counted them and a statement that never
// wrote them would leave the door judging every byte as the server's.
func TestTheFlushCarriesBrowserBytesInTheirOwnColumn(t *testing.T) {
	columns := columnsOf([]Row{
		{OrgID: testOrg, ProjectID: testProject, Day: testReceivedAt, Counts: Counts{Bytes: 300, BrowserBytes: 100}},
		{OrgID: testOrg, ProjectID: "project-2", Day: testReceivedAt, Counts: Counts{Bytes: 50}},
	})

	if !slices.Equal(columns.browserBytes, []int64{100, 0}) {
		t.Errorf("browser_bytes column = %v, want [100 0]", columns.browserBytes)
	}
	if !slices.Equal(columns.bytes, []int64{300, 50}) {
		t.Errorf("bytes column = %v, want [300 50]: the whole count, browser bytes included",
			columns.bytes)
	}
}

// ------------------------------------------------------ the billing unit

// The unit ADR 0056 prices on. One export can carry several services, and each
// one of them is billable.
func TestEveryServiceInATraceExportIsMetered(t *testing.T) {
	metered, err := countSpans(spansFrom(t, 2, "checkout", "payments"))
	if err != nil {
		t.Fatalf("counting spans: %v", err)
	}
	if got := metered.Services; !equalNames(got, []string{"checkout", "payments"}) {
		t.Errorf("services = %v, want checkout and payments", got)
	}
	if metered.Spans != 4 {
		t.Errorf("spans = %d, want 4", metered.Spans)
	}
}

// A service repeated across resources is one service, not two: the bill counts
// distinct names.
func TestTheSameServiceTwiceInOneExportIsOneService(t *testing.T) {
	metered, err := countSpans(spansFrom(t, 1, "checkout", "checkout"))
	if err != nil {
		t.Fatalf("counting spans: %v", err)
	}
	if len(metered.Services) != 1 {
		t.Errorf("services = %v, want one", metered.Services)
	}
}

// An SDK sending an empty resource block is not a service the customer ran.
// Billing for it would charge for silence.
func TestAResourceCarryingNothingIsNotABillableService(t *testing.T) {
	metered, err := countSpans(spansFrom(t, 0, "checkout"))
	if err != nil {
		t.Fatalf("counting spans: %v", err)
	}
	if len(metered.Services) != 0 {
		t.Errorf("services = %v, want none: the resource carried no spans", metered.Services)
	}
}

// Every resource with no `service.name` folds into one bucket, so a sender that
// omits the attribute is one service rather than one per message.
func TestResourcesWithNoServiceNameAreOneBucket(t *testing.T) {
	metered, err := countSpans(spansFrom(t, 1, "", ""))
	if err != nil {
		t.Fatalf("counting spans: %v", err)
	}
	if !equalNames(metered.Services, []string{UnnamedService}) {
		t.Errorf("services = %v, want the single %s bucket", metered.Services, UnnamedService)
	}
}

// OTLP puts no limit on an attribute value and the column is varchar(255). The
// truncation counts runes, not bytes, or a multi-byte name would be cut through
// a character and stored as broken UTF-8.
func TestAnOverlongServiceNameIsTruncatedOnARuneBoundary(t *testing.T) {
	name := strings.Repeat("é", maxServiceNameChars+10)

	metered, err := countSpans(spansFrom(t, 1, name))
	if err != nil {
		t.Fatalf("counting spans: %v", err)
	}
	stored := metered.Services[0]
	if got := utf8.RuneCountInString(stored); got != maxServiceNameChars {
		t.Errorf("stored name is %d runes, want %d", got, maxServiceNameChars)
	}
	if !utf8.ValidString(stored) {
		t.Error("truncation cut through a character and produced invalid UTF-8")
	}
}

// A service that only logs is still a service monitored, and so is one that
// only reports metrics. All three signals feed the same set.
func TestLogsAndMetricsAlsoMeterTheirServices(t *testing.T) {
	logged, err := countLogs(logsFrom(t, 3, "billing"))
	if err != nil {
		t.Fatalf("counting logs: %v", err)
	}
	if !equalNames(logged.Services, []string{"billing"}) {
		t.Errorf("log services = %v, want billing", logged.Services)
	}

	measured, err := countMetricPoints(metricsFrom(t, 2, "ledger"))
	if err != nil {
		t.Fatalf("counting metric points: %v", err)
	}
	if !equalNames(measured.Services, []string{"ledger"}) {
		t.Errorf("metric services = %v, want ledger", measured.Services)
	}
}

// equalNames compares two name sets regardless of order.
func equalNames(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	left, right := slices.Clone(got), slices.Clone(want)
	slices.Sort(left)
	slices.Sort(right)
	return slices.Equal(left, right)
}
