package auth

import (
	"context"
	"net/http"
)

// Where each door is willing to read a credential from, and what resolves it.
//
// This is a decision about the credential, not about the transport, so it lives
// here rather than in whichever handler happens to call it.
//
// **The server door reads the header and nothing else.** A secret key in a URL
// is a secret key in an access log, in a proxy's log, and in anything that
// records request lines. That is true no matter how convenient the query string
// would be, so the secret authenticator does not look at it.
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
// that is. Reading the key is therefore a method on the authenticator that
// resolves it, not a free function a door could pair with the wrong table: the
// type that reads `site_keys` is the only type that can read a URL.

// SiteKeyQueryParam carries a public key on a request that could not set a
// header. Named for the kind it holds, so pasting a secret key into it reads
// wrong before it fails.
const SiteKeyQueryParam = "nexovue_site_key"

// Gatekeeper is gate one as a mounted door needs it: where a request carries its
// key, and what that key resolves to.
//
// Both authenticators satisfy it and neither can satisfy the other's half. A
// door is handed exactly one of them when it is mounted, so nothing on the
// request path ever chooses an authenticator by inspecting the key presented.
// That branch is what two tables exist to prevent and it must not
// reappear one level up.
type Gatekeeper interface {
	PresentedKey(*http.Request) string
	Authenticate(ctx context.Context, presented string) (Identity, error)
}

// PresentedKey returns the secret ingest key a server request carries.
//
// Header only, deliberately. See the file comment above: there is no query
// parameter for this kind and there should not be one.
func (a *Authenticator) PresentedKey(r *http.Request) string {
	return r.Header.Get(HeaderName)
}

// PresentedKey returns the public key a browser request carries, from the header
// if it set one and from the query string otherwise.
//
// A value in the query string is returned only when it carries the public
// scheme. A secret key pasted there is therefore invisible to this method, not
// merely refused later — so a caller that logs what it read cannot log one,
// which matters because the receiver's credential redaction only covers
// connection-string passwords and would not catch a key in a URL.
func (a *SiteAuthenticator) PresentedKey(r *http.Request) string {
	if presented := r.Header.Get(HeaderName); presented != "" {
		return presented
	}
	fromQuery := r.URL.Query().Get(SiteKeyQueryParam)
	if checkSiteKeyFormat(fromQuery) != nil {
		return ""
	}
	return fromQuery
}
