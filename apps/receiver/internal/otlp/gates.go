package otlp

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"time"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/genproto/googleapis/rpc/code"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/tracesplit"
)

// The gate chain, written once for every transport. HTTP and gRPC differ only
// in where the credential is read from and how a verdict is rendered on the
// wire; what the gates decide is the same code for both, which is what keeps a
// second transport from becoming a second pipeline.

// refusal is a gate's verdict in OTLP's own terms, before a transport renders
// it. The rpc code is the protocol's, not HTTP's, because it is the one both
// transports can express.
type refusal struct {
	code    code.Code
	message string

	// retryAfter is how long the client should wait before sending the batch
	// again. Zero means the failure is not worth retrying.
	retryAfter time.Duration

	// cause is what went wrong on our side, for the log rather than the wire:
	// the caller is told the fixed message, never our internals.
	cause error
}

func refuse(rpcCode code.Code, message string) *refusal {
	return &refusal{code: rpcCode, message: message}
}

func refuseUntil(rpcCode code.Code, message string, retryAfter time.Duration) *refusal {
	return &refusal{code: rpcCode, message: message, retryAfter: retryAfter}
}

// because attaches the cause a log line should carry.
func (r *refusal) because(err error) *refusal {
	r.cause = err
	return r
}

// authenticate is gate one. It returns a context carrying the tenancy the key
// grants; nothing past it runs for a caller we cannot name.
func (o Options) authenticate(ctx context.Context, presentedKey string) (context.Context, *refusal) {
	identity, err := o.Authenticator.Authenticate(ctx, presentedKey)
	if err != nil {
		if auth.IsCallerFault(err) {
			return ctx, refuse(code.Code_UNAUTHENTICATED, messageUnauthenticated)
		}
		// The control plane is unreachable, which is our fault, not the
		// caller's: an unavailable answer is retryable where a rejection is not.
		return ctx, refuseUntil(code.Code_UNAVAILABLE, messageUnavailable, brokerRetryAfter).
			because(err)
	}

	ctx = auth.NewContext(ctx, identity)
	return logging.WithFields(ctx, logging.Fields{
		OrgID:     identity.OrgID,
		ProjectID: identity.ProjectID,
	}), nil
}

// throttle is gate two. It answers before the payload is decoded, which is the
// whole reason it sits above decoding rather than inside the exporter.
func (o Options) throttle(ctx context.Context) *refusal {
	identity, ok := auth.FromContext(ctx)
	if !ok {
		// Only reachable if the gate order were rewired.
		return refuse(code.Code_INTERNAL, messageUnavailable).
			because(errors.New("rate limit gate ran before authentication"))
	}

	decision, err := o.Limiter.Allow(ctx, identity.KeyPrefix)
	if err != nil {
		// Fail open. A Valkey blip must not turn into an ingest outage: losing
		// a customer's telemetry costs more than briefly losing the protection,
		// and the broker and payload caps still bound the damage.
		if o.Traffic.Degraded(reasonLimiterDown) {
			o.Logger.WarnContext(ctx, "rate limiter unavailable, allowing request",
				slog.String(fieldReason, reasonLimiterDown),
				slog.String(fieldError, logging.RedactCredentials(err.Error())))
		}
		return nil
	}
	if !decision.Allowed {
		return refuseUntil(code.Code_RESOURCE_EXHAUSTED, messageThrottled, decision.RetryAfter)
	}
	return nil
}

// acceptTraces is the last two gates, normalize and produce. It returns only
// once the broker holds every envelope, so a transport never acknowledges a
// batch the receiver failed to hand on.
func (o Options) acceptTraces(
	ctx context.Context,
	receivedAt time.Time,
	request *collectorpb.ExportTraceServiceRequest,
) *refusal {
	groups, err := tracesplit.ByTraceID(request)
	if err != nil {
		return refuse(code.Code_INVALID_ARGUMENT, err.Error())
	}

	identity, _ := auth.FromContext(ctx)
	if err := o.Publisher.PublishTraces(ctx, identity, receivedAt, groups); err != nil {
		return refuseUntil(code.Code_UNAVAILABLE, messageBrokerDown, brokerRetryAfter).
			because(err)
	}
	return nil
}

// spanCount is how much telemetry one export carried. It is what the summary
// counts, because a batch is not a unit anyone cares about losing — spans are.
func spanCount(request *collectorpb.ExportTraceServiceRequest) int {
	spans := 0
	for _, resource := range request.GetResourceSpans() {
		for _, scope := range resource.GetScopeSpans() {
			spans += len(scope.GetSpans())
		}
	}
	return spans
}

// record is the door's one accounting point: every export ends here, accepted or
// refused, and the summary is written from what it counted.
//
// A refusal writes at most one line per reason per summary window, at the level
// the control plane's request log would give its status: ERROR for a fault of
// ours, WARN for the caller's. The rest are counted — a broker outage refusing
// every request must not also flood the log it would be diagnosed from.
func (o Options) record(ctx context.Context, path string, spans int, refused *refusal) {
	httpStatus := http.StatusOK
	if refused != nil {
		httpStatus = httpStatusFor(refused.code)
	}
	attrs := []any{
		slog.String(fieldMethod, http.MethodPost),
		slog.String(fieldPath, path),
		slog.Int(fieldStatus, httpStatus),
		slog.Int(fieldSpans, spans),
		slog.Int64(fieldDurationMillis, time.Since(startedAt(ctx)).Milliseconds()),
	}

	if refused == nil {
		o.Traffic.Accepted(spans)
		o.Logger.DebugContext(ctx, acceptedMessage, attrs...)
		return
	}

	reason := reasonFor(refused.code)
	if !o.Traffic.Refused(reason, spans) {
		return
	}
	attrs = append(attrs, slog.String(fieldReason, reason))
	if refused.cause != nil {
		attrs = append(attrs,
			slog.String(fieldError, logging.RedactCredentials(refused.cause.Error())))
	}

	if httpStatus >= http.StatusInternalServerError {
		o.Logger.ErrorContext(ctx, refusedMessage, attrs...)
		return
	}
	o.Logger.WarnContext(ctx, refusedMessage, attrs...)
}

// startedKey carries when a request entered the door, so a refusal written by
// any gate reports the whole-request duration the control plane's log reports,
// not just the part after the gate that turned it away.
type startedKey struct{}

func withStart(ctx context.Context, at time.Time) context.Context {
	return context.WithValue(ctx, startedKey{}, at)
}

// startedAt falls back to now, which reports a zero duration rather than an
// invented one, if a transport ever forgets to stamp the request.
func startedAt(ctx context.Context) time.Time {
	if at, ok := ctx.Value(startedKey{}).(time.Time); ok {
		return at
	}
	return time.Now()
}
