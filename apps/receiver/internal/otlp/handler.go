// Package otlp is door 1: the OTLP endpoint customer SDKs export to, over both
// transports the protocol defines — HTTP on 4318 and gRPC on 4317 — for all
// three signals it defines: traces, logs and metrics.
//
// Gates run in order and the order is load-bearing:
//
//	auth -> origin -> allowance -> ratelimit -> decode -> normalize -> produce
//
// Authentication comes first so no endpoint ever exists unauthenticated. The
// origin gate is next because the list it checks belongs to the key, and
// because a request from an origin the key does not cover must not spend the
// owner's rate budget. The allowance gate follows for the same reason and one
// more: it answers from an in-process cache, so an organization whose plan
// month is spent is refused without a Valkey round trip. The limiter comes
// before the payload is read so a tenant over its limit costs us a Valkey round
// trip rather than a megabyte of parsing. The gates live in gates.go and every
// door runs the same ones over both transports: only where the credential is
// read from and how a verdict is written differ.
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

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/allowance"
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

// The plan refusal names the date, because "when does this come back" is the
// only question a customer has left and the answer is not guessable from the
// code. It says the allowance was spent rather than that the rate was exceeded:
// both are RESOURCE_EXHAUSTED, and only one of them clears in a second.
const messageAllowanceSpent = "the included monthly ingest volume for this plan is spent; " +
	"ingestion resumes on "

// allowanceResetLayout is a plain calendar date. Not RFC 3339: the allowance
// returns at the start of a UTC day, and an instant would invite a customer to
// read a timezone into it that is not there.
const allowanceResetLayout = time.DateOnly

func allowanceSpentMessage(resetsOn time.Time) string {
	return messageAllowanceSpent + resetsOn.UTC().Format(allowanceResetLayout)
}

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
	// Gatekeeper is the one credential this door accepts, fixed when it is
	// mounted. Which one it is decides the table, the key scheme and whether a
	// URL may carry the key at all — see auth/presented.go.
	Gatekeeper auth.Gatekeeper
	Limiter    *ratelimit.Limiter

	// Allowance is the plan gate: whether this organization has volume left in
	// the month its plan includes. Shared by both doors and both transports —
	// the boundary belongs to the organization, not to the entrance.
	Allowance *allowance.Checker

	Publisher *ingest.Publisher
	Logger    *slog.Logger
	Traffic   *Traffic

	// MaxBodyBytes caps the request as it arrives; MaxDecodedBodyBytes caps
	// what a compressed one may expand to.
	MaxBodyBytes        int64
	MaxDecodedBodyBytes int64

	// accepts is the set of request encodings this door reads. Unexported and
	// filled by Handler, so what a door accepts is decided by which entrance it
	// is rather than by whoever constructs the Options — see contentTypes.
	accepts contentTypes
}

func (o Options) limits() bodyLimits {
	return bodyLimits{wire: o.MaxBodyBytes, decoded: o.MaxDecodedBodyBytes}
}

// BrowserPathPrefix is where the browser door's three OTLP paths hang, so an
// SDK pointed at <host>/browser reaches them the way the protocol's own
// endpoint rules build a URL.
//
// A separate path rather than the server paths served by whichever
// authenticator the presented key looks like it wants. Choosing per request is
// the branch ADR 0086's two tables removed, one level up: a route mounted with
// one Gatekeeper cannot have it, so a secret key on a browser path and a public
// key on a server path both fail the scheme check before any lookup runs. It is
// also what confines the query-string read — the only place a credential may
// arrive outside a header — to routes that can resolve nothing but public keys.
const BrowserPathPrefix = "/browser"

// Doors are the two entrances the one OTLP/HTTP listener serves. Named rather
// than ordered: swapping them would put the secret door on the path a web page
// reaches.
type Doors struct {
	Server  Options
	Browser Options
}

// Handler serves the OTLP/HTTP endpoints behind the gate chain: the server door
// on the paths the protocol defines, and the browser door on the same three
// under BrowserPathPrefix.
func Handler(doors Doors) http.Handler {
	mux := http.NewServeMux()
	mountSignals(mux, "", doors.Server, serverContentTypes)
	mountSignals(mux, BrowserPathPrefix, doors.Browser, browserContentTypes)
	return mux
}

// mountSignals fixes what one entrance reads at the point it is mounted, which
// is the only place both entrances are named together and so the only place a
// difference between them can be read off in one line.
func mountSignals(mux *http.ServeMux, prefix string, opts Options, accepts contentTypes) {
	opts.accepts = accepts
	route(mux, prefix, opts, tracesDoor)
	route(mux, prefix, opts, logsDoor)
	route(mux, prefix, opts, metricsDoor)
}

// route mounts one signal's endpoint behind the full chain. POST carries every
// export — the only method OTLP/HTTP defines — and OPTIONS exists solely so a
// browser can ask permission to send one.
//
// The prefix is folded into the endpoint rather than only into the pattern, so
// the path a request is logged and counted under is the path it arrived on.
func route[Request proto.Message](
	mux *http.ServeMux,
	prefix string,
	opts Options,
	signal signalDoor[Request],
) {
	signal.httpPath = prefix + signal.httpPath
	mux.Handle("POST "+signal.httpPath,
		answeringCORS(stamped(authenticate(opts, signal.endpoint,
			fromAllowedOrigin(opts, signal.endpoint,
				withinAllowance(opts, signal.endpoint,
					throttle(opts, signal.endpoint,
						export(opts, signal))))))))
	mux.Handle("OPTIONS "+signal.httpPath, http.HandlerFunc(preflight))
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

// authenticate reads the credential out of the request the way this door's
// gatekeeper is willing to read it, and hands the verdict to the shared gate.
func authenticate(opts Options, at endpoint, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, refused := opts.authenticate(r.Context(), opts.Gatekeeper.PresentedKey(r))
		if refused != nil {
			opts.record(ctx, at.httpPath, at.unit, itemsNotRead, refused)
			writeRefusal(w, opts.accepts.replyCodec(r), refused)
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
			writeRefusal(w, opts.accepts.replyCodec(r), refused)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// withinAllowance is the plan gate: the organization's month, spent or not.
func withinAllowance(opts Options, at endpoint, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if refused := opts.withinAllowance(r.Context()); refused != nil {
			opts.record(r.Context(), at.httpPath, at.unit, itemsNotRead, refused)
			writeRefusal(w, opts.accepts.replyCodec(r), refused)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func throttle(opts Options, at endpoint, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if refused := opts.throttle(r.Context()); refused != nil {
			opts.record(r.Context(), at.httpPath, at.unit, itemsNotRead, refused)
			writeRefusal(w, opts.accepts.replyCodec(r), refused)
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

		encoding, err := opts.accepts.codecFor(r.Header.Get(headerContentType))
		if err != nil {
			// One refusal, written and logged, so the wire and the line cannot
			// disagree about a status OTLP/HTTP spells more precisely than the
			// rpc code does.
			refused := refuse(code.Code_INVALID_ARGUMENT, err.Error()).
				answeredAs(http.StatusUnsupportedMediaType).because(err)
			opts.record(r.Context(), door.httpPath, door.unit, itemsNotRead, refused)
			writeRefusal(w, protobufCodec, refused)
			return
		}

		body, err := readBody(w, r, opts.limits())
		if err != nil {
			refused := bodyRefusal(err)
			opts.record(r.Context(), door.httpPath, door.unit, itemsNotRead, refused)
			writeRefusal(w, encoding, refused)
			return
		}

		request := door.newRequest()
		if err := encoding.unmarshal(body, request); err != nil {
			refused := refuse(code.Code_INVALID_ARGUMENT, door.malformedMessage).because(err)
			opts.record(r.Context(), door.httpPath, door.unit, itemsNotRead, refused)
			writeRefusal(w, encoding, refused)
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

// bodyRefusal renders a body failure as the sentinel's own wording rather than
// the wrapped cause: a decompression error's text is our internals, not the
// caller's business. The cause still travels for the log.
func bodyRefusal(err error) *refusal {
	switch {
	case errors.Is(err, errBodyTooLarge):
		// Non-retryable: the same oversized batch would fail again.
		return refuse(code.Code_INVALID_ARGUMENT, errBodyTooLarge.Error()).
			answeredAs(http.StatusRequestEntityTooLarge).because(err)
	case errors.Is(err, errUnsupportedContentEncode):
		return refuse(code.Code_INVALID_ARGUMENT, errUnsupportedContentEncode.Error()).
			answeredAs(http.StatusUnsupportedMediaType).because(err)
	default:
		return refuse(code.Code_INVALID_ARGUMENT, errUnreadableBody.Error()).because(err)
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
		refused := refuse(code.Code_INTERNAL, messageUnavailable).because(err)
		o.record(r.Context(), at.httpPath, at.unit, items, refused)
		writeRefusal(w, encoding, refused)
		return
	}
	o.record(r.Context(), at.httpPath, at.unit, items, nil)
	w.Header().Set(headerContentType, encoding.contentType)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}
