// Package otlp is door 1: the OTLP/HTTP endpoint customer SDKs export to.
//
// Gates run in order and the order is load-bearing:
//
//	auth -> ratelimit -> decode -> normalize -> produce
//
// Authentication comes first so the endpoint never exists unauthenticated, and
// the limiter comes before the body is read so a tenant over its limit costs us
// a Valkey round trip rather than a megabyte of parsing.
//
// Failures answer in OTLP's own terms rather than problem+json: the callers are
// SDKs, and the protocol defines what they will understand.
package otlp

import (
	"errors"
	"log/slog"
	"net/http"
	"time"

	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/genproto/googleapis/rpc/code"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ingest"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/logging"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ratelimit"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/tracesplit"
)

// TracesPath is the OTLP/HTTP endpoint for the SPANS signal, fixed by the spec.
const TracesPath = "/v1/traces"

// brokerRetryAfter is what a client is told to wait when the broker would not
// take the batch. Long enough to be worth a pause, short enough that a restart
// does not idle the sender.
const brokerRetryAfter = 5 * time.Second

// Messages are deliberately vague about credentials: telling an unauthenticated
// caller whether a key was missing, malformed or merely unknown helps only an
// attacker enumerating keys.
const (
	messageUnauthenticated = "invalid or missing " + auth.HeaderName + " header"
	messageThrottled       = "ingest rate limit exceeded for this key"
	messageBrokerDown      = "ingestion buffer unavailable, retry"
	messageUnavailable     = "receiver cannot serve this request, retry"
)

// Options are the collaborators and limits the door needs. All are required.
type Options struct {
	Authenticator *auth.Authenticator
	Limiter       *ratelimit.Limiter
	Publisher     *ingest.Publisher
	Logger        *slog.Logger

	// MaxBodyBytes caps the request as it arrives; MaxDecodedBodyBytes caps
	// what a compressed one may expand to.
	MaxBodyBytes        int64
	MaxDecodedBodyBytes int64
}

func (o Options) limits() bodyLimits {
	return bodyLimits{wire: o.MaxBodyBytes, decoded: o.MaxDecodedBodyBytes}
}

// Handler serves the OTLP endpoints behind the gate chain.
func Handler(opts Options) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("POST "+TracesPath, authenticate(opts, throttle(opts, traces(opts))))
	return mux
}

// authenticate is gate one. Nothing past it runs for a caller we cannot name.
func authenticate(opts Options, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		identity, err := opts.Authenticator.Authenticate(r.Context(), r.Header.Get(auth.HeaderName))
		if err != nil {
			if auth.IsCallerFault(err) {
				writeStatus(w, replyCodec(r), http.StatusUnauthorized,
					code.Code_UNAUTHENTICATED, messageUnauthenticated)
				return
			}
			// The control plane is unreachable, which is our fault, not the
			// caller's: 503 is retryable where 401 is not.
			opts.Logger.ErrorContext(r.Context(), "cannot verify ingest key",
				slog.String("error", logging.RedactCredentials(err.Error())))
			writeRetryableStatus(w, replyCodec(r), http.StatusServiceUnavailable,
				code.Code_UNAVAILABLE, messageUnavailable, brokerRetryAfter)
			return
		}

		ctx := auth.NewContext(r.Context(), identity)
		ctx = logging.WithFields(ctx, logging.Fields{
			OrgID:     identity.OrgID,
			ProjectID: identity.ProjectID,
		})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// throttle is gate two. It answers before the body is read, which is the whole
// reason it sits above decoding rather than inside the handler.
func throttle(opts Options, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		identity, ok := auth.FromContext(r.Context())
		if !ok {
			// Only reachable if the gate order were rewired.
			opts.Logger.ErrorContext(r.Context(), "rate limit gate ran before authentication")
			writeStatus(w, replyCodec(r), http.StatusInternalServerError,
				code.Code_INTERNAL, messageUnavailable)
			return
		}

		decision, err := opts.Limiter.Allow(r.Context(), identity.KeyPrefix)
		if err != nil {
			// Fail open. A Valkey blip must not turn into an ingest outage:
			// losing a customer's telemetry costs more than briefly losing the
			// protection, and the broker and body caps still bound the damage.
			opts.Logger.WarnContext(r.Context(), "rate limiter unavailable, allowing request",
				slog.String("error", logging.RedactCredentials(err.Error())))
			next.ServeHTTP(w, r)
			return
		}
		if !decision.Allowed {
			writeRetryableStatus(w, replyCodec(r), http.StatusTooManyRequests,
				code.Code_RESOURCE_EXHAUSTED, messageThrottled, decision.RetryAfter)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// traces is the remaining three gates: decode, normalize, produce.
func traces(opts Options) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAt := time.Now()

		encoding, err := codecFor(r.Header.Get(headerContentType))
		if err != nil {
			writeStatus(w, protobufCodec, http.StatusUnsupportedMediaType,
				code.Code_INVALID_ARGUMENT, err.Error())
			return
		}

		body, err := readBody(w, r, opts.limits())
		if err != nil {
			writeBodyFailure(w, encoding, err)
			return
		}

		var request collectorpb.ExportTraceServiceRequest
		if err := encoding.unmarshal(body, &request); err != nil {
			writeStatus(w, encoding, http.StatusBadRequest,
				code.Code_INVALID_ARGUMENT, "request is not a valid ExportTraceServiceRequest")
			return
		}

		groups, err := tracesplit.ByTraceID(&request)
		if err != nil {
			writeStatus(w, encoding, http.StatusBadRequest, code.Code_INVALID_ARGUMENT, err.Error())
			return
		}

		identity, _ := auth.FromContext(r.Context())
		if err := opts.Publisher.PublishTraces(r.Context(), identity, receivedAt, groups); err != nil {
			opts.Logger.ErrorContext(r.Context(), "cannot publish trace batch",
				slog.Int("traces", len(groups)),
				slog.String("error", logging.RedactCredentials(err.Error())))
			writeRetryableStatus(w, encoding, http.StatusServiceUnavailable,
				code.Code_UNAVAILABLE, messageBrokerDown, brokerRetryAfter)
			return
		}

		writeAccepted(w, encoding)
	})
}

// writeBodyFailure answers with the sentinel's own wording rather than the
// wrapped cause: a decompression error's text is our internals, not the
// caller's business.
func writeBodyFailure(w http.ResponseWriter, encoding codec, err error) {
	switch {
	case errors.Is(err, errBodyTooLarge):
		// Non-retryable: the same oversized batch would fail again.
		writeStatus(w, encoding, http.StatusRequestEntityTooLarge,
			code.Code_INVALID_ARGUMENT, errBodyTooLarge.Error())
	case errors.Is(err, errUnsupportedContentEncode):
		writeStatus(w, encoding, http.StatusUnsupportedMediaType,
			code.Code_INVALID_ARGUMENT, errUnsupportedContentEncode.Error())
	default:
		writeStatus(w, encoding, http.StatusBadRequest,
			code.Code_INVALID_ARGUMENT, errUnreadableBody.Error())
	}
}

// writeAccepted is OTLP's success: an ExportTraceServiceResponse with
// partial_success left unset, which is what "we took all of it" means.
func writeAccepted(w http.ResponseWriter, encoding codec) {
	body, err := encoding.marshal(&collectorpb.ExportTraceServiceResponse{})
	if err != nil {
		writeStatus(w, encoding, http.StatusInternalServerError,
			code.Code_INTERNAL, messageUnavailable)
		return
	}
	w.Header().Set(headerContentType, encoding.contentType)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}
