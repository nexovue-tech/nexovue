// Package otlp is door 1: the OTLP endpoint customer SDKs export to, over both
// transports the protocol defines — HTTP on 4318 and gRPC on 4317 — for all
// three signals it defines: traces, logs and metrics.
//
// Gates run in order and the order is load-bearing:
//
//	auth -> origin -> ratelimit -> decode -> normalize -> produce
//
// Authentication comes first so no endpoint ever exists unauthenticated. The
// origin gate is next because the list it checks belongs to the key, and
// because a request from an origin the key does not cover must not spend the
// owner's rate budget. The limiter comes before the payload is read so a tenant
// over its limit costs us a Valkey round trip rather than a megabyte of
// parsing. The gates live in
// gates.go and every door runs the same ones over both transports: only where
// the credential is read from and how a verdict is written differ.
//
// What each signal does differently — the request it decodes, the unit it
// counts, whether it is split before publishing — lives in signals.go, and is
// the only place a fourth signal would touch.
//
// Failures answer in OTLP's own terms rather than problem+json: the callers are
// SDKs, and the protocol defines what they will understand.
package otlp

import (
	"errors"
	"log/slog"
	"net/http"
	"time"

	"google.golang.org/genproto/googleapis/rpc/code"
	"google.golang.org/protobuf/proto"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ingest"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ratelimit"
)

// brokerRetryAfter is what a client is told to wait when the broker would not
// take the batch. Long enough to be worth a pause, short enough that a restart
// does not idle the sender.
const brokerRetryAfter = 5 * time.Second

// Messages are deliberately vague about credentials: telling an unauthenticated
// caller whether a key was missing, malformed or merely unknown helps only an
// attacker enumerating keys.
const (
	messageUnauthenticated = "invalid or missing " + auth.HeaderName + " credential"
	messageThrottled       = "ingest rate limit exceeded for this key"
	messageBrokerDown      = "ingestion buffer unavailable, retry"
	messageUnavailable     = "receiver cannot serve this request, retry"
)

// The browser refusals say more than the credential ones do, and on purpose:
// the caller already holds the key, so naming what is wrong with the request
// tells an attacker nothing and tells a customer onboarding a site everything.
// A CORS refusal is invisible to page script, so a vague message here is a
// customer with no way to find their own mistake.
const (
	messageOriginNotAllowed       = "this ingest key is not allowed from this origin"
	messagePublicKeyWithoutOrigin = "public ingest key presented without a browser Origin"
	messageNotABrowserKey         = "this ingest key cannot be used from a browser; a public site key is what a web page sends"
)

// Options are the collaborators and limits the door needs. All are required.
type Options struct {
	Authenticator *auth.Authenticator
	Limiter       *ratelimit.Limiter
	Publisher     *ingest.Publisher
	Logger        *slog.Logger
	Traffic       *Traffic

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
	route(mux, opts, tracesDoor)
	route(mux, opts, logsDoor)
	route(mux, opts, metricsDoor)
	return mux
}

// route mounts one signal's endpoint behind the full chain. POST carries every
// export — the only method OTLP/HTTP defines — and OPTIONS exists solely so a
// browser can ask permission to send one.
func route[Request proto.Message](mux *http.ServeMux, opts Options, door signalDoor[Request]) {
	mux.Handle("POST "+door.httpPath,
		answeringCORS(stamped(authenticate(opts, door.endpoint,
			fromAllowedOrigin(opts, door.endpoint,
				throttle(opts, door.endpoint,
					export(opts, door)))))))
	mux.Handle("OPTIONS "+door.httpPath, http.HandlerFunc(preflight))
}

// stamped records when the request entered the door and who sent it, so every
// gate below reports the same whole-request duration and names the same caller —
// including the gates that answer before there is a tenant to name.
func stamped(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := withCaller(withStart(r.Context(), time.Now()), callerOf(r))
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// authenticate reads the credential out of the request header and hands the
// verdict to the shared gate.
func authenticate(opts Options, at endpoint, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, refused := opts.authenticate(r.Context(), r.Header.Get(auth.HeaderName))
		if refused != nil {
			opts.record(ctx, at.httpPath, at.unit, itemsNotRead, refused)
			writeRefusal(w, replyCodec(r), refused)
			return
		}
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// fromAllowedOrigin is the browser gate: the key's own list decides whether the
// page that sent this export may use it.
func fromAllowedOrigin(opts Options, at endpoint, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if refused := opts.checkOrigin(r.Context(), r.Header.Get(headerOrigin)); refused != nil {
			opts.record(r.Context(), at.httpPath, at.unit, itemsNotRead, refused)
			writeRefusal(w, replyCodec(r), refused)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func throttle(opts Options, at endpoint, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if refused := opts.throttle(r.Context()); refused != nil {
			opts.record(r.Context(), at.httpPath, at.unit, itemsNotRead, refused)
			writeRefusal(w, replyCodec(r), refused)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// itemsNotRead is what an export refused before its body was read contributes.
// Each refused_<unit> count is therefore a floor, not a total — the
// refused_requests count beside it is the one that covers every refusal.
const itemsNotRead = 0

// export is the remaining three gates: decode, normalize, produce. Decoding is
// the transport's own work — over gRPC the framework does it — so it lives here
// rather than in the shared chain.
func export[Request proto.Message](opts Options, door signalDoor[Request]) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAt := time.Now()

		encoding, err := codecFor(r.Header.Get(headerContentType))
		if err != nil {
			// Answered as 415 rather than the code's usual 400, so writeStatus
			// is called directly; the tally still sees it.
			opts.record(r.Context(), door.httpPath, door.unit, itemsNotRead,
				refuse(code.Code_INVALID_ARGUMENT, err.Error()).because(err))
			writeStatus(w, protobufCodec, http.StatusUnsupportedMediaType,
				code.Code_INVALID_ARGUMENT, err.Error())
			return
		}

		body, err := readBody(w, r, opts.limits())
		if err != nil {
			opts.record(r.Context(), door.httpPath, door.unit, itemsNotRead,
				refuse(code.Code_INVALID_ARGUMENT, messageUnreadableRequest).because(err))
			writeBodyFailure(w, encoding, err)
			return
		}

		request := door.newRequest()
		if err := encoding.unmarshal(body, request); err != nil {
			opts.record(r.Context(), door.httpPath, door.unit, itemsNotRead,
				refuse(code.Code_INVALID_ARGUMENT, door.malformedMessage).because(err))
			writeStatus(w, encoding, http.StatusBadRequest,
				code.Code_INVALID_ARGUMENT, door.malformedMessage)
			return
		}

		items := door.countItems(request)
		if refused := door.accept(r.Context(), opts, receivedAt, request); refused != nil {
			opts.record(r.Context(), door.httpPath, door.unit, items, refused)
			writeRefusal(w, encoding, refused)
			return
		}

		opts.writeAccepted(w, r, encoding, door.endpoint, door.newResponse(), items)
	})
}

// messageUnreadableRequest stands in for whichever body failure writeBodyFailure
// renders: the tally cares that the request was unreadable, the caller gets the
// specific sentinel's own wording.
const messageUnreadableRequest = "request body could not be accepted"

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

// writeAccepted is OTLP's success: the signal's own Export…ServiceResponse with
// partial_success left unset, which is what "we took all of it" means.
func (o Options) writeAccepted(
	w http.ResponseWriter,
	r *http.Request,
	encoding codec,
	at endpoint,
	response proto.Message,
	items int,
) {
	body, err := encoding.marshal(response)
	if err != nil {
		// The batch is published but the caller will never hear so. Counted as
		// our fault, because that is what it is.
		o.record(r.Context(), at.httpPath, at.unit, items,
			refuse(code.Code_INTERNAL, messageUnavailable).because(err))
		writeStatus(w, encoding, http.StatusInternalServerError,
			code.Code_INTERNAL, messageUnavailable)
		return
	}
	o.record(r.Context(), at.httpPath, at.unit, items, nil)
	w.Header().Set(headerContentType, encoding.contentType)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}
