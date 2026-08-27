// Package otlp is door 1: the OTLP endpoint customer SDKs export to, over both
// transports the protocol defines — HTTP on 4318 and gRPC on 4317.
//
// Gates run in order and the order is load-bearing:
//
//	auth -> ratelimit -> decode -> normalize -> produce
//
// Authentication comes first so the endpoint never exists unauthenticated, and
// the limiter comes before the payload is read so a tenant over its limit costs
// us a Valkey round trip rather than a megabyte of parsing. The gates live in
// gates.go and both transports run the same ones: only where the credential is
// read from and how a verdict is written differ.
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
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ratelimit"
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
	messageUnauthenticated  = "invalid or missing " + auth.HeaderName + " credential"
	messageThrottled        = "ingest rate limit exceeded for this key"
	messageBrokerDown       = "ingestion buffer unavailable, retry"
	messageUnavailable      = "receiver cannot serve this request, retry"
	messageMalformedPayload = "request is not a valid ExportTraceServiceRequest"
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

// Handler serves the OTLP/HTTP endpoints behind the gate chain.
func Handler(opts Options) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("POST "+TracesPath, authenticate(opts, throttle(opts, traces(opts))))
	return mux
}

// authenticate reads the credential out of the request header and hands the
// verdict to the shared gate.
func authenticate(opts Options, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, refused := opts.authenticate(r.Context(), r.Header.Get(auth.HeaderName))
		if refused != nil {
			writeRefusal(w, replyCodec(r), refused)
			return
		}
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func throttle(opts Options, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if refused := opts.throttle(r.Context()); refused != nil {
			writeRefusal(w, replyCodec(r), refused)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// traces is the remaining three gates: decode, normalize, produce. Decoding is
// the transport's own work — over gRPC the framework does it — so it lives here
// rather than in the shared chain.
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
				code.Code_INVALID_ARGUMENT, messageMalformedPayload)
			return
		}

		if refused := opts.acceptTraces(r.Context(), receivedAt, &request); refused != nil {
			writeRefusal(w, encoding, refused)
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
