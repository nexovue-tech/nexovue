package auth

import "net/http"

// Where each door is willing to read a credential from.
//
// This is a decision about the credential, not about the transport, so it lives
// here rather than in whichever handler happens to call it (ADR 0086).
//
// **The server door reads the header and nothing else.** A secret key in a URL
// is a secret key in an access log, in a proxy's log, and in anything that
// records request lines. That is true no matter how convenient the query string
// would be, so `KeyFromRequest` does not look at it.
//
// **The browser door also reads a query parameter**, because it has to.
// `navigator.sendBeacon` is the only way to flush telemetry as a page unloads —
// which is exactly when a visitor's session data is most likely to be lost — and
// it cannot set request headers at all. Sentry and Datadog both answer this the
// same way. For a value already printed in the page's own source, appearing in a
// URL discloses nothing new.
//
// The two rules are not symmetric, and the asymmetry is the point: query strings
// are acceptable for a credential that was never secret and unacceptable for one
// that is.

// SiteKeyQueryParam carries a public key on a request that could not set a
// header. Named for the kind it holds, so pasting a secret key into it reads
// wrong before it fails.
const SiteKeyQueryParam = "nexovue_site_key"

// KeyFromRequest returns the secret ingest key a server request presents.
//
// Header only, deliberately. See the package comment above: there is no query
// parameter for this kind and there should not be one.
func KeyFromRequest(r *http.Request) string {
	return r.Header.Get(HeaderName)
}

// SiteKeyFromRequest returns the public key a browser request presents, from the
// header if it set one and from the query string otherwise.
//
// A value in the query string is returned only when it carries the public
// scheme. A secret key pasted there is therefore invisible to this function, not
// merely refused later — so a caller that logs what it read cannot log one,
// which matters because the receiver's credential redaction only covers
// connection-string passwords and would not catch a key in a URL.
func SiteKeyFromRequest(r *http.Request) string {
	if presented := r.Header.Get(HeaderName); presented != "" {
		return presented
	}
	fromQuery := r.URL.Query().Get(SiteKeyQueryParam)
	if checkSiteKeyFormat(fromQuery) != nil {
		return ""
	}
	return fromQuery
}
