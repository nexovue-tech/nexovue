package otlp

import (
	"context"
	"time"

	logscollectorpb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metricscollectorpb "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	tracecollectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/durationpb"

	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/genproto/googleapis/rpc/errdetails"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
)

// OTLP/gRPC's three collector services, served over the same gates as the HTTP
// doors. They hold no state of their own: everything they decide lives in
// gates.go and signals.go, which is what keeps the second transport from
// becoming a second pipeline.

type traceExporter struct {
	tracecollectorpb.UnimplementedTraceServiceServer
	opts Options
}

func (e *traceExporter) Export(
	ctx context.Context,
	request *tracecollectorpb.ExportTraceServiceRequest,
) (*tracecollectorpb.ExportTraceServiceResponse, error) {
	if err := runExport(ctx, e.opts, tracesDoor, request); err != nil {
		return nil, err
	}
	// OTLP's success: partial_success left unset means everything was accepted.
	return &tracecollectorpb.ExportTraceServiceResponse{}, nil
}

type logExporter struct {
	logscollectorpb.UnimplementedLogsServiceServer
	opts Options
}

func (e *logExporter) Export(
	ctx context.Context,
	request *logscollectorpb.ExportLogsServiceRequest,
) (*logscollectorpb.ExportLogsServiceResponse, error) {
	if err := runExport(ctx, e.opts, logsDoor, request); err != nil {
		return nil, err
	}
	return &logscollectorpb.ExportLogsServiceResponse{}, nil
}

type metricExporter struct {
	metricscollectorpb.UnimplementedMetricsServiceServer
	opts Options
}

func (e *metricExporter) Export(
	ctx context.Context,
	request *metricscollectorpb.ExportMetricsServiceRequest,
) (*metricscollectorpb.ExportMetricsServiceResponse, error) {
	if err := runExport(ctx, e.opts, metricsDoor, request); err != nil {
		return nil, err
	}
	return &metricscollectorpb.ExportMetricsServiceResponse{}, nil
}

// runExport runs the gate chain over an already-decoded batch — decoding is
// grpc-go's work on this transport — and answers in the protocol's own terms.
func runExport[Request proto.Message](
	ctx context.Context,
	opts Options,
	door signalDoor[Request],
	request Request,
) error {
	receivedAt := time.Now()
	ctx = withCaller(withStart(ctx, receivedAt), callerOfRPC(ctx))

	ctx, refused := opts.authenticate(ctx, presentedKey(ctx))
	if refused != nil {
		opts.record(ctx, door.grpcPath, door.unit, itemsNotRead, refused)
		return refused.grpcError()
	}
	if refused := opts.throttle(ctx); refused != nil {
		opts.record(ctx, door.grpcPath, door.unit, itemsNotRead, refused)
		return refused.grpcError()
	}

	items := door.countItems(request)
	if refused := door.accept(ctx, opts, receivedAt, request); refused != nil {
		opts.record(ctx, door.grpcPath, door.unit, items, refused)
		return refused.grpcError()
	}

	opts.record(ctx, door.grpcPath, door.unit, items, nil)
	return nil
}

// registerExporters mounts all three collector services on the server.
func registerExporters(server *grpc.Server, opts Options) {
	traces := &traceExporter{opts: opts}
	tracesDesc := serviceDesc(tracecollectorpb.TraceService_ServiceDesc, opts, tracesDoor,
		func(ctx context.Context, request *tracecollectorpb.ExportTraceServiceRequest) (any, error) {
			return traces.Export(ctx, request)
		})
	server.RegisterService(&tracesDesc, traces)

	logs := &logExporter{opts: opts}
	logsDesc := serviceDesc(logscollectorpb.LogsService_ServiceDesc, opts, logsDoor,
		func(ctx context.Context, request *logscollectorpb.ExportLogsServiceRequest) (any, error) {
			return logs.Export(ctx, request)
		})
	server.RegisterService(&logsDesc, logs)

	metrics := &metricExporter{opts: opts}
	metricsDesc := serviceDesc(metricscollectorpb.MetricsService_ServiceDesc, opts, metricsDoor,
		func(ctx context.Context, request *metricscollectorpb.ExportMetricsServiceRequest) (any, error) {
			return metrics.Export(ctx, request)
		})
	server.RegisterService(&metricsDesc, metrics)
}

// serviceDesc is a generated collector descriptor with its one method handler
// replaced. Everything that names the service still comes from the generated
// descriptor, so regenerating the proto cannot leave this behind.
//
// The handler translates the one thing the generated one passes straight
// through: grpc-go answers a payload it cannot decode with INTERNAL, which says
// the fault is ours and leaves an SDK nothing to act on, where OTLP's answer for
// a payload the server cannot read is INVALID_ARGUMENT, and it is not retryable.
func serviceDesc[Request proto.Message](
	generated grpc.ServiceDesc,
	opts Options,
	door signalDoor[Request],
	export func(context.Context, Request) (any, error),
) grpc.ServiceDesc {
	handle := func(
		service any,
		ctx context.Context,
		decode func(any) error,
		interceptor grpc.UnaryServerInterceptor,
	) (any, error) {
		request := door.newRequest()
		if err := decode(request); err != nil {
			// Our own wording, not grpc-go's, which quotes our internals back.
			refused := refuse(code.Code_INVALID_ARGUMENT, door.malformedMessage).because(err)
			stamped := withCaller(withStart(ctx, time.Now()), callerOfRPC(ctx))
			opts.record(stamped, door.grpcPath, door.unit, itemsNotRead, refused)
			return nil, refused.grpcError()
		}

		if interceptor == nil {
			return export(ctx, request)
		}
		info := &grpc.UnaryServerInfo{Server: service, FullMethod: door.grpcPath}
		return interceptor(ctx, request, info, func(ctx context.Context, req any) (any, error) {
			batch, isBatch := req.(Request)
			if !isBatch {
				return nil, status.Error(codes.Internal, messageUnavailable)
			}
			return export(ctx, batch)
		})
	}

	generated.Methods = []grpc.MethodDesc{{MethodName: exportMethod, Handler: handle}}
	return generated
}

// presentedKey reads the ingest credential out of the call's metadata. gRPC has
// no headers, but the key travels under the name it is documented with; gRPC
// lower-cases metadata keys, which is the form auth.HeaderName is already in.
func presentedKey(ctx context.Context) string {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return ""
	}
	values := md.Get(auth.HeaderName)
	if len(values) == 0 {
		return ""
	}
	return values[0]
}

// grpcError renders a gate's verdict as a gRPC status.
func (r *refusal) grpcError() error {
	failure := status.New(grpcCodeFor(r.code), r.message)
	if r.retryAfter <= 0 {
		return failure.Err()
	}

	// OTLP asks a retryable status to carry RetryInfo, so the client waits the
	// delay we chose instead of guessing one.
	detailed, err := failure.WithDetails(&errdetails.RetryInfo{
		RetryDelay: durationpb.New(r.retryAfter),
	})
	if err != nil {
		// The verdict still has to reach the caller; only the hint is lost.
		return failure.Err()
	}
	return detailed.Err()
}

// grpcCodeFor names each verdict in grpc-go's own enum. The two enums are both
// google.rpc.Code and their values already agree, but spelling the pairs out
// keeps them from being cast blind — and mirrors httpStatusFor, which the other
// transport needs for the same job. Only codes the gates produce are listed;
// anything else is our bug, and INTERNAL is what a bug means.
func grpcCodeFor(rpcCode code.Code) codes.Code {
	switch rpcCode {
	case code.Code_UNAUTHENTICATED:
		return codes.Unauthenticated
	case code.Code_RESOURCE_EXHAUSTED:
		return codes.ResourceExhausted
	case code.Code_INVALID_ARGUMENT:
		return codes.InvalidArgument
	case code.Code_UNAVAILABLE:
		return codes.Unavailable
	default:
		return codes.Internal
	}
}
