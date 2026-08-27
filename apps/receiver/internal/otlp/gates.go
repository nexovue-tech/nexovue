package otlp

import (
	"context"
	"log/slog"
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
}

func refuse(rpcCode code.Code, message string) *refusal {
	return &refusal{code: rpcCode, message: message}
}

func refuseUntil(rpcCode code.Code, message string, retryAfter time.Duration) *refusal {
	return &refusal{code: rpcCode, message: message, retryAfter: retryAfter}
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
		o.Logger.ErrorContext(ctx, "cannot verify ingest key",
			slog.String("error", logging.RedactCredentials(err.Error())))
		return ctx, refuseUntil(code.Code_UNAVAILABLE, messageUnavailable, brokerRetryAfter)
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
		o.Logger.ErrorContext(ctx, "rate limit gate ran before authentication")
		return refuse(code.Code_INTERNAL, messageUnavailable)
	}

	decision, err := o.Limiter.Allow(ctx, identity.KeyPrefix)
	if err != nil {
		// Fail open. A Valkey blip must not turn into an ingest outage: losing
		// a customer's telemetry costs more than briefly losing the protection,
		// and the broker and payload caps still bound the damage.
		o.Logger.WarnContext(ctx, "rate limiter unavailable, allowing request",
			slog.String("error", logging.RedactCredentials(err.Error())))
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
		o.Logger.ErrorContext(ctx, "cannot publish trace batch",
			slog.Int("traces", len(groups)),
			slog.String("error", logging.RedactCredentials(err.Error())))
		return refuseUntil(code.Code_UNAVAILABLE, messageBrokerDown, brokerRetryAfter)
	}
	return nil
}
