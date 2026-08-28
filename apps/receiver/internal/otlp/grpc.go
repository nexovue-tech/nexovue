package otlp

import (
	"context"
	"time"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"

	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/genproto/googleapis/rpc/errdetails"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
)

// exportMethod is the only method TraceService defines. Named so the descriptor
// below and the generated one cannot drift apart silently.
const exportMethod = "Export"

// traceExporter is OTLP/gRPC's TraceService, served over the same gates as the
// HTTP door. It holds no state of its own: everything it decides lives in
// gates.go, which is what keeps the second transport from becoming a second
// pipeline.
type traceExporter struct {
	collectorpb.UnimplementedTraceServiceServer
	opts Options
}

// Export runs the gate chain over an already-decoded batch — decoding is
// grpc-go's work on this transport — and answers in the protocol's own terms.
func (e *traceExporter) Export(
	ctx context.Context,
	request *collectorpb.ExportTraceServiceRequest,
) (*collectorpb.ExportTraceServiceResponse, error) {
	receivedAt := time.Now()
	ctx = withStart(ctx, receivedAt)

	ctx, refused := e.opts.authenticate(ctx, presentedKey(ctx))
	if refused != nil {
		e.opts.record(ctx, exportPath, spansNotRead, refused)
		return nil, refused.grpcError()
	}
	if refused := e.opts.throttle(ctx); refused != nil {
		e.opts.record(ctx, exportPath, spansNotRead, refused)
		return nil, refused.grpcError()
	}

	spans := spanCount(request)
	if refused := e.opts.acceptTraces(ctx, receivedAt, request); refused != nil {
		e.opts.record(ctx, exportPath, spans, refused)
		return nil, refused.grpcError()
	}

	// OTLP's success: partial_success left unset means everything was accepted.
	e.opts.record(ctx, exportPath, spans, nil)
	return &collectorpb.ExportTraceServiceResponse{}, nil
}

// exportPath is the HTTP/2 path a gRPC export is a POST to. Logging it in the
// same field the HTTP door logs /v1/traces in is what lets one query answer
// "what did the receiver do" across both transports.
var exportPath = "/" + collectorpb.TraceService_ServiceDesc.ServiceName + "/" + exportMethod

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

// serviceDesc is the generated TraceService descriptor with its one method
// handler replaced. Everything that names the service still comes from the
// generated descriptor, so regenerating the proto cannot leave this behind.
func serviceDesc() grpc.ServiceDesc {
	desc := collectorpb.TraceService_ServiceDesc
	desc.Methods = []grpc.MethodDesc{{MethodName: exportMethod, Handler: handleExport}}
	return desc
}

// handleExport mirrors the generated unary handler, translating the one thing
// the generated one passes straight through. grpc-go answers a payload it
// cannot decode with INTERNAL, which says the fault is ours and leaves an SDK
// nothing to act on; OTLP's answer for a payload the server cannot read is
// INVALID_ARGUMENT, and it is not retryable.
func handleExport(
	service any,
	ctx context.Context,
	decode func(any) error,
	interceptor grpc.UnaryServerInterceptor,
) (any, error) {
	exporter, ok := service.(*traceExporter)
	if !ok {
		return nil, status.Error(codes.Internal, messageUnavailable)
	}

	request := new(collectorpb.ExportTraceServiceRequest)
	if err := decode(request); err != nil {
		// Our own wording, not grpc-go's, which quotes our internals back.
		refused := refuse(code.Code_INVALID_ARGUMENT, messageMalformedPayload).because(err)
		exporter.opts.record(withStart(ctx, time.Now()), exportPath, spansNotRead, refused)
		return nil, refused.grpcError()
	}

	if interceptor == nil {
		return exporter.Export(ctx, request)
	}
	info := &grpc.UnaryServerInfo{
		Server:     service,
		FullMethod: exportPath,
	}
	return interceptor(ctx, request, info, func(ctx context.Context, req any) (any, error) {
		batch, isBatch := req.(*collectorpb.ExportTraceServiceRequest)
		if !isBatch {
			return nil, status.Error(codes.Internal, messageUnavailable)
		}
		return exporter.Export(ctx, batch)
	})
}
