package otlp

import (
	"net/http"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
)

// CORS is what makes a browser willing to send an export at all, and what makes
// a refusal readable when it will not. An OTLP export is a cross-origin POST
// carrying a content type outside the safelist and a credential header the
// browser has never heard of, so it is preflighted: nothing reaches the door
// until an OPTIONS has been answered.

const (
	headerOrigin        = "Origin"
	headerVary          = "Vary"
	headerAllowOrigin   = "Access-Control-Allow-Origin"
	headerAllowMethods  = "Access-Control-Allow-Methods"
	headerAllowHeaders  = "Access-Control-Allow-Headers"
	headerExposeHeaders = "Access-Control-Expose-Headers"
	headerMaxAge        = "Access-Control-Max-Age"
	headerAllow         = "Allow"
)

// allowedRequestHeaders is exactly what the door reads off an export, and
// nothing else. Echoing back whatever the browser listed in
// Access-Control-Request-Headers would answer "yes" to headers we do not
// accept, which is an allowlist that allows everything.
const allowedRequestHeaders = "content-type, content-encoding, accept, " + auth.HeaderName

// allowedMethods is POST alone — the only method OTLP/HTTP defines.
const allowedMethods = http.MethodPost

// exposedResponseHeaders is the one response header a refusal carries meaning
// in. Without it a throttled browser SDK can see the 429 and not how long to
// wait, so it retries on its own schedule into a door already saying stop.
const exposedResponseHeaders = headerRetryAfter

// preflightMaxAge caches the preflight for two hours, which is Chromium's own
// ceiling — Firefox allows 24, and the default when the header is absent is
// five seconds. A browser SDK flushing every few seconds would otherwise send
// two requests for every export it makes, for the whole session.
const preflightMaxAge = "7200"

// preflight answers the OPTIONS a browser sends before an export.
//
// It is deliberately permissive, and that is the load-bearing part: a preflight
// carries no credential — the browser announces the key's header *name*, never
// its value — so the door cannot know whose key this will be, let alone which
// origins that key allows. Refusing here would turn away every browser before
// the request that could actually be judged is ever sent, and the page would be
// left with an opaque CORS failure and nothing to act on.
//
// So the preflight answers for the *shape* of the request, and the POST behind
// it answers for the origin, in words the page can read.
func preflight(w http.ResponseWriter, r *http.Request) {
	w.Header().Set(headerVary, headerOrigin)

	origin := r.Header.Get(headerOrigin)
	if origin == "" {
		// Not a preflight, just an OPTIONS. Answer what the path serves.
		w.Header().Set(headerAllow, allowedMethods+", "+http.MethodOptions)
		w.WriteHeader(http.StatusNoContent)
		return
	}

	w.Header().Set(headerAllowOrigin, origin)
	w.Header().Set(headerAllowMethods, allowedMethods)
	w.Header().Set(headerAllowHeaders, allowedRequestHeaders)
	w.Header().Set(headerMaxAge, preflightMaxAge)
	w.WriteHeader(http.StatusNoContent)
}

// answeringCORS puts the headers a browser needs on every reply to a request
// that carried an Origin — the refusals included.
//
// A refusal with no Access-Control-Allow-Origin is invisible to the page that
// sent it: the browser reports a CORS failure and swallows the status and the
// message. That is the difference between a customer being told their origin is
// not on the list and a customer with nothing to go on.
//
// The origin is echoed rather than answered with "*", and credentials are never
// allowed, so no cookie or client certificate is ever carried on an export.
// What echoing exposes is bounded by what the door returns, which is a constant
// either way: an empty OTLP response, or a fixed google.rpc.Status. The
// receiver holds nothing readable.
func answeringCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set(headerVary, headerOrigin)
		if origin := r.Header.Get(headerOrigin); origin != "" {
			w.Header().Set(headerAllowOrigin, origin)
			w.Header().Set(headerExposeHeaders, exposedResponseHeaders)
		}
		next.ServeHTTP(w, r)
	})
}
