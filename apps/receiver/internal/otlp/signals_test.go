package otlp_test

import (
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	logscollectorpb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricscollectorpb "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/otlp"
)

// The logs and metrics doors run the gates the traces door runs, over both
// transports, and differ in one thing: an export forwards whole, because nothing
// downstream reassembles a log or metric batch.

// --- fixtures ----------------------------------------------------------------

const schemaURL = "https://opentelemetry.io/schemas/1.43.0"

// logsExport carries records under two resources, which is what proves the batch
// is forwarded rather than split.
func logsExport(bodies ...string) *logscollectorpb.ExportLogsServiceRequest {
	resources := make([]*logspb.ResourceLogs, 0, len(bodies))
	for _, body := range bodies {
		resources = append(resources, &logspb.ResourceLogs{
			SchemaUrl: schemaURL,
			ScopeLogs: []*logspb.ScopeLogs{{
				SchemaUrl: schemaURL,
				LogRecords: []*logspb.LogRecord{{
					SeverityText: body,
					TraceId:      traceID(0x55),
					SpanId:       make([]byte, 8),
				}},
			}},
		})
	}
	return &logscollectorpb.ExportLogsServiceRequest{ResourceLogs: resources}
}

// metricsExport is one gauge carrying points data points.
func metricsExport(points int) *metricscollectorpb.ExportMetricsServiceRequest {
	dataPoints := make([]*metricspb.NumberDataPoint, points)
	for i := range dataPoints {
		dataPoints[i] = &metricspb.NumberDataPoint{
			Value: &metricspb.NumberDataPoint_AsInt{AsInt: int64(i)},
		}
	}
	return &metricscollectorpb.ExportMetricsServiceRequest{
		ResourceMetrics: []*metricspb.ResourceMetrics{{
			SchemaUrl: schemaURL,
			ScopeMetrics: []*metricspb.ScopeMetrics{{
				Metrics: []*metricspb.Metric{{
					Name: "http.server.duration",
					Data: &metricspb.Metric_Gauge{
						Gauge: &metricspb.Gauge{DataPoints: dataPoints},
					},
				}},
			}},
		}},
	}
}

func encoded(t *testing.T, message proto.Message) []byte {
	t.Helper()
	body, err := proto.Marshal(message)
	if err != nil {
		t.Fatalf("encoding request: %v", err)
	}
	return body
}

// publishedEnvelope decodes the one record a forwarded batch produces.
func publishedEnvelope(t *testing.T, producer *countingProducer, wantTopic string) *nexovuev1.Envelope {
	t.Helper()
	if len(producer.records) != 1 {
		t.Fatalf("produced %d records, want exactly 1: an export forwards whole",
			len(producer.records))
	}
	record := producer.records[0]
	if record.Topic != wantTopic {
		t.Errorf("topic = %q, want %q", record.Topic, wantTopic)
	}
	if string(record.Key) != orgID {
		t.Errorf("key = %q, want the tenant id %q", record.Key, orgID)
	}

	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(record.Value, &envelope); err != nil {
		t.Fatalf("record is not an Envelope: %v", err)
	}
	return &envelope
}

// --- the happy path over HTTP ------------------------------------------------

func TestLogsExportIsForwardedWholeToTheLogsTopic(t *testing.T) {
	d := newDoor(t, doorOptions{})
	request := logsExport("ERROR", "WARN")

	response := postTo(t, d, otlp.LogsPath, encoded(t, request), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if got := response.Header().Get("Content-Type"); got != contentTypeProtobuf {
		t.Errorf("Content-Type = %q, want the request's %q", got, contentTypeProtobuf)
	}
	var accepted logscollectorpb.ExportLogsServiceResponse
	if err := proto.Unmarshal(response.Body.Bytes(), &accepted); err != nil {
		t.Fatalf("success body is not an ExportLogsServiceResponse: %v", err)
	}
	if accepted.GetPartialSuccess() != nil {
		t.Errorf("partial_success = %v, want unset: everything was accepted",
			accepted.GetPartialSuccess())
	}

	envelope := publishedEnvelope(t, d.producer, logsTopic)
	if envelope.GetSignal() != nexovuev1.Signal_SIGNAL_LOGS {
		t.Errorf("signal = %v, want SIGNAL_LOGS", envelope.GetSignal())
	}
	if envelope.GetDoor() != nexovuev1.Door_DOOR_OTLP {
		t.Errorf("door = %v, want DOOR_OTLP", envelope.GetDoor())
	}
	if envelope.GetTenantId() != orgID || envelope.GetProjectId() != projectID {
		t.Errorf("envelope tenancy = %s/%s, want %s/%s",
			envelope.GetTenantId(), envelope.GetProjectId(), orgID, projectID)
	}
	if envelope.GetSemconvVersion() != "1.43.0" {
		t.Errorf("semconv_version = %q, want the version the payload declared",
			envelope.GetSemconvVersion())
	}

	var payload logscollectorpb.ExportLogsServiceRequest
	if err := proto.Unmarshal(envelope.GetPayload(), &payload); err != nil {
		t.Fatalf("payload is not an ExportLogsServiceRequest: %v", err)
	}
	if !proto.Equal(&payload, request) {
		t.Error("the payload changed on the way through; logs forward as received")
	}
}

func TestMetricsExportIsForwardedWholeToTheMetricsTopic(t *testing.T) {
	d := newDoor(t, doorOptions{})
	request := metricsExport(3)

	response := postTo(t, d, otlp.MetricsPath, encoded(t, request), nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	var accepted metricscollectorpb.ExportMetricsServiceResponse
	if err := proto.Unmarshal(response.Body.Bytes(), &accepted); err != nil {
		t.Fatalf("success body is not an ExportMetricsServiceResponse: %v", err)
	}
	if accepted.GetPartialSuccess() != nil {
		t.Errorf("partial_success = %v, want unset", accepted.GetPartialSuccess())
	}

	envelope := publishedEnvelope(t, d.producer, metricsTopic)
	if envelope.GetSignal() != nexovuev1.Signal_SIGNAL_METRICS {
		t.Errorf("signal = %v, want SIGNAL_METRICS", envelope.GetSignal())
	}

	var payload metricscollectorpb.ExportMetricsServiceRequest
	if err := proto.Unmarshal(envelope.GetPayload(), &payload); err != nil {
		t.Fatalf("payload is not an ExportMetricsServiceRequest: %v", err)
	}
	if !proto.Equal(&payload, request) {
		t.Error("the payload changed on the way through; metrics forward as received")
	}
}

// OTLP/JSON encodes trace and span ids as hex where protobuf's JSON mapping uses
// base64, and the spec applies that to LogRecord as much as to Span. A log line
// whose trace id did not survive the door cannot be correlated to its trace,
// which is the whole point of carrying one.
func TestJSONLogsExportKeepsHexTraceIDs(t *testing.T) {
	d := newDoor(t, doorOptions{})

	response := postTo(t, d, otlp.LogsPath, []byte(hexIDLogsExport),
		map[string]string{"Content-Type": contentTypeJSON})

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
	}
	if got := response.Header().Get("Content-Type"); got != contentTypeJSON {
		t.Errorf("Content-Type = %q, want the request's %q", got, contentTypeJSON)
	}

	var payload logscollectorpb.ExportLogsServiceRequest
	envelope := publishedEnvelope(t, d.producer, logsTopic)
	if err := proto.Unmarshal(envelope.GetPayload(), &payload); err != nil {
		t.Fatalf("payload is not an ExportLogsServiceRequest: %v", err)
	}
	record := payload.GetResourceLogs()[0].GetScopeLogs()[0].GetLogRecords()[0]
	if !bytes.Equal(record.GetTraceId(), traceID(0x55)) {
		t.Errorf("trace_id = %x, want %x: a hex id must decode to the 16 bytes it names",
			record.GetTraceId(), traceID(0x55))
	}
	if len(record.GetSpanId()) != 8 {
		t.Errorf("span_id is %d bytes, want 8", len(record.GetSpanId()))
	}
}

// hexIDLogsExport is what a spec-compliant OTLP/JSON exporter sends: ids as
// hex, which protojson would otherwise read as base64 and decode to the wrong
// width.
const hexIDLogsExport = `{
  "resourceLogs": [{
    "schemaUrl": "https://opentelemetry.io/schemas/1.43.0",
    "scopeLogs": [{
      "logRecords": [{
        "severityText": "ERROR",
        "traceId": "55555555555555555555555555555555",
        "spanId": "0000000000000000"
      }]
    }]
  }]
}`

func TestEmptyLogsAndMetricsExportsArePublishedAsEmptyBatches(t *testing.T) {
	cases := map[string]struct {
		path    string
		body    proto.Message
		topic   string
		signal  nexovuev1.Signal
		decoded proto.Message
	}{
		"logs": {
			path:   otlp.LogsPath,
			body:   &logscollectorpb.ExportLogsServiceRequest{},
			topic:  logsTopic,
			signal: nexovuev1.Signal_SIGNAL_LOGS,
		},
		"metrics": {
			path:   otlp.MetricsPath,
			body:   &metricscollectorpb.ExportMetricsServiceRequest{},
			topic:  metricsTopic,
			signal: nexovuev1.Signal_SIGNAL_METRICS,
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			response := postTo(t, d, tc.path, encoded(t, tc.body), nil)

			if response.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
			}
			// A trace batch with no spans splits to nothing and publishes
			// nothing; a logs or metrics export forwards whatever arrived,
			// even when the counter made it zero. The counter is accounting,
			// never a publish condition: it knows the metric shapes OTLP
			// defines today, and a shape added tomorrow would count zero —
			// which must not become a batch dropped in silence.
			if envelope := publishedEnvelope(t, d.producer, tc.topic); envelope.GetSignal() != tc.signal {
				t.Errorf("signal = %v, want %v", envelope.GetSignal(), tc.signal)
			}
		})
	}
}

// --- failures over HTTP ------------------------------------------------------

func TestNewDoorsRefuseAnUnusableKeyWithoutReachingTheBroker(t *testing.T) {
	for name, path := range map[string]string{"logs": otlp.LogsPath, "metrics": otlp.MetricsPath} {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			response := postTo(t, d, path, encoded(t, logsExport("ERROR")),
				map[string]string{auth.HeaderName: "nvk_live_absentabsentabsentabse"})

			if response.Code != http.StatusUnauthorized {
				t.Fatalf("status = %d, want %d", response.Code, http.StatusUnauthorized)
			}
			if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_UNAUTHENTICATED) {
				t.Errorf("status code = %d, want UNAUTHENTICATED", got)
			}
			if len(d.producer.records) != 0 {
				t.Error("an unauthenticated request reached the broker")
			}
		})
	}
}

func TestNewDoorsRefuseAMalformedPayload(t *testing.T) {
	for name, path := range map[string]string{"logs": otlp.LogsPath, "metrics": otlp.MetricsPath} {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})

			response := postTo(t, d, path, []byte("this is not protobuf at all"), nil)

			if response.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want %d", response.Code, http.StatusBadRequest)
			}
			refusal := rpcStatus(t, response)
			if refusal.GetCode() != int32(code.Code_INVALID_ARGUMENT) {
				t.Errorf("status code = %d, want INVALID_ARGUMENT", refusal.GetCode())
			}
			if refusal.GetMessage() == "" {
				t.Error("a failure must say why")
			}
			if len(d.producer.records) != 0 {
				t.Error("a malformed payload reached the broker")
			}
		})
	}
}

func TestNewDoorsRefuseABrokerOutageAsRetryable(t *testing.T) {
	d := newDoor(t, doorOptions{produceErr: errors.New("kafka: not leader for partition")})

	response := postTo(t, d, otlp.LogsPath, encoded(t, logsExport("ERROR")), nil)

	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d: the request must not be acked",
			response.Code, http.StatusServiceUnavailable)
	}
	if response.Header().Get("Retry-After") == "" {
		t.Error("a 503 must tell the client how long to wait")
	}
	if got := rpcStatus(t, response).GetCode(); got != int32(code.Code_UNAVAILABLE) {
		t.Errorf("status code = %d, want UNAVAILABLE", got)
	}
}

// --- both transports ---------------------------------------------------------

func TestGRPCLogsExportIsForwardedWhole(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{})

	response, err := logscollectorpb.NewLogsServiceClient(d.conn).
		Export(callContext(t, validKey), logsExport("ERROR", "WARN"))
	if err != nil {
		t.Fatalf("export: %v", err)
	}
	if response.GetPartialSuccess() != nil {
		t.Errorf("partial_success = %v, want unset", response.GetPartialSuccess())
	}

	if envelope := publishedEnvelope(t, d.producer, logsTopic); envelope.
		GetSignal() != nexovuev1.Signal_SIGNAL_LOGS {
		t.Errorf("signal = %v, want SIGNAL_LOGS", envelope.GetSignal())
	}
}

func TestGRPCMetricsExportIsForwardedWhole(t *testing.T) {
	d := newGRPCDoor(t, doorOptions{})

	response, err := metricscollectorpb.NewMetricsServiceClient(d.conn).
		Export(callContext(t, validKey), metricsExport(2))
	if err != nil {
		t.Fatalf("export: %v", err)
	}
	if response.GetPartialSuccess() != nil {
		t.Errorf("partial_success = %v, want unset", response.GetPartialSuccess())
	}

	if envelope := publishedEnvelope(t, d.producer, metricsTopic); envelope.
		GetSignal() != nexovuev1.Signal_SIGNAL_METRICS {
		t.Errorf("signal = %v, want SIGNAL_METRICS", envelope.GetSignal())
	}
}

// Nothing listens unauthenticated, on either transport, at any door.
func TestGRPCNewServicesRefuseAnUnusableCredential(t *testing.T) {
	t.Run("logs", func(t *testing.T) {
		d := newGRPCDoor(t, doorOptions{})

		_, err := logscollectorpb.NewLogsServiceClient(d.conn).
			Export(callContext(t, ""), logsExport("ERROR"))

		if got := grpcStatus(t, err).Code(); got != codes.Unauthenticated {
			t.Errorf("code = %v, want %v", got, codes.Unauthenticated)
		}
		if len(d.producer.records) != 0 {
			t.Error("an unauthenticated export reached the broker")
		}
	})
	t.Run("metrics", func(t *testing.T) {
		d := newGRPCDoor(t, doorOptions{})

		_, err := metricscollectorpb.NewMetricsServiceClient(d.conn).
			Export(callContext(t, ""), metricsExport(1))

		if got := grpcStatus(t, err).Code(); got != codes.Unauthenticated {
			t.Errorf("code = %v, want %v", got, codes.Unauthenticated)
		}
		if len(d.producer.records) != 0 {
			t.Error("an unauthenticated export reached the broker")
		}
	})
}

// Bytes that are not the request the service expects are the caller's to fix, so
// the answer must be one an SDK will not retry — not grpc-go's INTERNAL.
func TestGRPCNewServicesRefuseAMalformedPayload(t *testing.T) {
	cases := map[string]string{
		"logs":    "/opentelemetry.proto.collector.logs.v1.LogsService/Export",
		"metrics": "/opentelemetry.proto.collector.metrics.v1.MetricsService/Export",
	}
	for name, method := range cases {
		t.Run(name, func(t *testing.T) {
			d := newGRPCDoor(t, doorOptions{})

			// Field 1, wire type 2, declaring 127 bytes that are not there.
			err := d.invokeRaw(t, method, []byte{0x0a, 0x7f})

			if got := grpcStatus(t, err).Code(); got != codes.InvalidArgument {
				t.Errorf("code = %v, want %v", got, codes.InvalidArgument)
			}
			if len(d.producer.records) != 0 {
				t.Error("a malformed payload reached the broker")
			}
		})
	}
}

// --- the summary -------------------------------------------------------------

// A door missing from the summary is a door that can fail silently, which is the
// one failure the summary exists to catch.
func TestEachDoorCountsInItsOwnUnit(t *testing.T) {
	cases := map[string]struct {
		path  string
		body  proto.Message
		field string
		want  float64
	}{
		"logs":    {otlp.LogsPath, logsExport("ERROR", "WARN"), "accepted_log_records", 2},
		"metrics": {otlp.MetricsPath, metricsExport(3), "accepted_data_points", 3},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			traffic := otlp.NewTraffic()
			d := newDoor(t, doorOptions{traffic: traffic})

			if response := postTo(t, d, tc.path, encoded(t, tc.body), nil); response.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d (%s)", response.Code, http.StatusOK, response.Body)
			}

			line := summaryLine(t, runSummary(t, traffic))
			if line[tc.field] != tc.want {
				t.Errorf("%s = %v, want %v", tc.field, line[tc.field], tc.want)
			}
		})
	}
}

// --- routing -----------------------------------------------------------------

func TestOnlyPostIsRoutedOnTheNewDoors(t *testing.T) {
	for name, path := range map[string]string{"logs": otlp.LogsPath, "metrics": otlp.MetricsPath} {
		t.Run(name, func(t *testing.T) {
			d := newDoor(t, doorOptions{})
			request := httptest.NewRequest(http.MethodGet, path, nil)
			request.Header.Set(auth.HeaderName, validKey)

			recorder := httptest.NewRecorder()
			d.handler.ServeHTTP(recorder, request)

			if recorder.Code == http.StatusOK {
				t.Errorf("GET %s was accepted; OTLP/HTTP is POST only", path)
			}
		})
	}
}
