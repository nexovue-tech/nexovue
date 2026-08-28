package otlp

import (
	"context"
	"time"

	logscollectorpb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricscollectorpb "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	tracecollectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	metricspb "go.opentelemetry.io/proto/otlp/metrics/v1"
	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ingest"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/semconv"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/tracesplit"
)

// The OTLP/HTTP endpoints, fixed by the spec: one path per signal, both
// transports on the ports the protocol reserves.
const (
	TracesPath  = "/v1/traces"
	LogsPath    = "/v1/logs"
	MetricsPath = "/v1/metrics"
)

// exportMethod is the only method each OTLP collector service defines. Named so
// the descriptors below and the generated ones cannot drift apart silently.
const exportMethod = "Export"

// endpoint is what the shared gates need to know about a door whatever it
// carries: where each transport logs it, and what its telemetry is counted in.
// It is not generic, which is what keeps authentication and rate limiting one
// piece of code rather than one per signal.
type endpoint struct {
	httpPath string
	grpcPath string
	unit     Unit
}

// signalDoor is everything one signal does differently: the request it decodes,
// the response it answers with, how much telemetry a batch carries, and how an
// accepted batch reaches the broker. Everything else — the gate chain, the OTLP
// error semantics, both transports — is shared, which is what keeps three doors
// from becoming three pipelines.
type signalDoor[Request proto.Message] struct {
	endpoint

	// malformedMessage names the request shape the caller failed to send, which
	// is the one thing a decode failure can usefully tell them.
	malformedMessage string

	newRequest  func() Request
	newResponse func() proto.Message
	countItems  func(Request) int

	// accept is the last two gates, normalize and produce. It returns only once
	// the broker holds the data, so no transport acknowledges a batch the
	// receiver failed to hand on.
	accept func(context.Context, Options, time.Time, Request) *refusal
}

var tracesDoor = signalDoor[*tracecollectorpb.ExportTraceServiceRequest]{
	endpoint: endpoint{
		httpPath: TracesPath,
		grpcPath: grpcPathOf(tracecollectorpb.TraceService_ServiceDesc),
		unit:     Spans,
	},
	malformedMessage: "request is not a valid ExportTraceServiceRequest",
	newRequest: func() *tracecollectorpb.ExportTraceServiceRequest {
		return new(tracecollectorpb.ExportTraceServiceRequest)
	},
	newResponse: func() proto.Message { return new(tracecollectorpb.ExportTraceServiceResponse) },
	countItems:  spanCount,
	accept:      acceptTraces,
}

var logsDoor = signalDoor[*logscollectorpb.ExportLogsServiceRequest]{
	endpoint: endpoint{
		httpPath: LogsPath,
		grpcPath: grpcPathOf(logscollectorpb.LogsService_ServiceDesc),
		unit:     LogRecords,
	},
	malformedMessage: "request is not a valid ExportLogsServiceRequest",
	newRequest: func() *logscollectorpb.ExportLogsServiceRequest {
		return new(logscollectorpb.ExportLogsServiceRequest)
	},
	newResponse: func() proto.Message { return new(logscollectorpb.ExportLogsServiceResponse) },
	countItems:  logRecordCount,
	accept:      acceptLogs,
}

var metricsDoor = signalDoor[*metricscollectorpb.ExportMetricsServiceRequest]{
	endpoint: endpoint{
		httpPath: MetricsPath,
		grpcPath: grpcPathOf(metricscollectorpb.MetricsService_ServiceDesc),
		unit:     DataPoints,
	},
	malformedMessage: "request is not a valid ExportMetricsServiceRequest",
	newRequest: func() *metricscollectorpb.ExportMetricsServiceRequest {
		return new(metricscollectorpb.ExportMetricsServiceRequest)
	},
	newResponse: func() proto.Message { return new(metricscollectorpb.ExportMetricsServiceResponse) },
	countItems:  dataPointCount,
	accept:      acceptMetrics,
}

// grpcPathOf is the HTTP/2 path a gRPC export is a POST to. Logging it in the
// same field the HTTP door logs /v1/traces in is what lets one query answer
// "what did the receiver do" across both transports.
func grpcPathOf(desc grpc.ServiceDesc) string {
	return "/" + desc.ServiceName + "/" + exportMethod
}

// acceptTraces splits before publishing, because trace assembly downstream
// depends on every span of a trace reaching one partition.
func acceptTraces(
	ctx context.Context,
	opts Options,
	receivedAt time.Time,
	request *tracecollectorpb.ExportTraceServiceRequest,
) *refusal {
	groups, err := tracesplit.ByTraceID(request)
	if err != nil {
		return refuse(code.Code_INVALID_ARGUMENT, err.Error())
	}

	identity, _ := auth.FromContext(ctx)
	if err := opts.Publisher.PublishTraces(ctx, identity, receivedAt, groups); err != nil {
		return refuseUntil(code.Code_UNAVAILABLE, messageBrokerDown, brokerRetryAfter).because(err)
	}
	return nil
}

// acceptLogs and acceptMetrics forward the export exactly as it arrived: nothing
// downstream reassembles a log or metric batch, so there is nothing for a split
// to buy and a re-encode on the hot path to lose.
func acceptLogs(
	ctx context.Context,
	opts Options,
	receivedAt time.Time,
	request *logscollectorpb.ExportLogsServiceRequest,
) *refusal {
	return acceptBatch(ctx, opts, receivedAt, ingest.Batch{
		Signal:         nexovuev1.Signal_SIGNAL_LOGS,
		SemconvVersion: logsSemconvVersion(request),
		Request:        request,
	})
}

func acceptMetrics(
	ctx context.Context,
	opts Options,
	receivedAt time.Time,
	request *metricscollectorpb.ExportMetricsServiceRequest,
) *refusal {
	return acceptBatch(ctx, opts, receivedAt, ingest.Batch{
		Signal:         nexovuev1.Signal_SIGNAL_METRICS,
		SemconvVersion: metricsSemconvVersion(request),
		Request:        request,
	})
}

func acceptBatch(
	ctx context.Context,
	opts Options,
	receivedAt time.Time,
	batch ingest.Batch,
) *refusal {
	identity, _ := auth.FromContext(ctx)
	if err := opts.Publisher.PublishBatch(ctx, identity, receivedAt, batch); err != nil {
		return refuseUntil(code.Code_UNAVAILABLE, messageBrokerDown, brokerRetryAfter).because(err)
	}
	return nil
}

// How much telemetry one export carried, in each signal's own unit. A batch is
// not something anyone cares about losing; these are — and they are the units
// OTLP's own partial_success counts rejections in.
//
// These counts are accounting only, never a condition on publishing. A metric
// shape OTLP adds later would count zero here, and a zero that suppressed the
// publish would be a batch dropped in silence.

func spanCount(request *tracecollectorpb.ExportTraceServiceRequest) int {
	spans := 0
	for _, resource := range request.GetResourceSpans() {
		for _, scope := range resource.GetScopeSpans() {
			spans += len(scope.GetSpans())
		}
	}
	return spans
}

func logRecordCount(request *logscollectorpb.ExportLogsServiceRequest) int {
	records := 0
	for _, resource := range request.GetResourceLogs() {
		for _, scope := range resource.GetScopeLogs() {
			records += len(scope.GetLogRecords())
		}
	}
	return records
}

func dataPointCount(request *metricscollectorpb.ExportMetricsServiceRequest) int {
	points := 0
	for _, resource := range request.GetResourceMetrics() {
		for _, scope := range resource.GetScopeMetrics() {
			for _, metric := range scope.GetMetrics() {
				points += pointsIn(metric)
			}
		}
	}
	return points
}

// pointsIn counts a metric's points across the five shapes OTLP defines. Only
// the shape actually set contributes: the getters answer nil for the rest.
func pointsIn(metric *metricspb.Metric) int {
	return len(metric.GetGauge().GetDataPoints()) +
		len(metric.GetSum().GetDataPoints()) +
		len(metric.GetHistogram().GetDataPoints()) +
		len(metric.GetExponentialHistogram().GetDataPoints()) +
		len(metric.GetSummary().GetDataPoints())
}

// The convention generation an export declares, for the envelope's
// semconv_version. A scope's schema URL describes the telemetry and wins; a
// resource's describes only its attributes and is the fallback. This is the
// precedence the trace door already applies per split trace.
//
// A batch forwarded whole may mix generations, and one field cannot say so. The
// first declared is reported and every schema URL survives untouched in the
// payload, so a writer that needs per-resource precision reads it there.

func logsSemconvVersion(request *logscollectorpb.ExportLogsServiceRequest) string {
	fallback := ""
	for _, resource := range request.GetResourceLogs() {
		for _, scope := range resource.GetScopeLogs() {
			if version := semconv.FromSchemaURLs(scope.GetSchemaUrl()); version != "" {
				return version
			}
		}
		if fallback == "" {
			fallback = semconv.FromSchemaURLs(resource.GetSchemaUrl())
		}
	}
	return fallback
}

func metricsSemconvVersion(request *metricscollectorpb.ExportMetricsServiceRequest) string {
	fallback := ""
	for _, resource := range request.GetResourceMetrics() {
		for _, scope := range resource.GetScopeMetrics() {
			if version := semconv.FromSchemaURLs(scope.GetSchemaUrl()); version != "" {
				return version
			}
		}
		if fallback == "" {
			fallback = semconv.FromSchemaURLs(resource.GetSchemaUrl())
		}
	}
	return fallback
}
