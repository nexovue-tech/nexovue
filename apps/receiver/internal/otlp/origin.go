package otlp

import (
	"context"
	"errors"
	"net/url"
	"strings"

	"google.golang.org/genproto/googleapis/rpc/code"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
)

// The browser gate. A public ingest key is printed in the page that uses it, so
// the key alone names nobody: anyone reading the source can copy it. What still
// ties it to its owner is the Origin the browser stamps on the request, because
// Origin is a forbidden request header name — page script cannot set it, and a
// browser will not lie about it.
//
// That is the whole of what this is worth, and it is worth saying plainly:
// **curl can send any Origin it likes.** This bounds abuse by other people's web
// pages, which is the realistic threat for a key lying in public; it is not an
// authentication boundary and must never be described as one. Sentry says the
// same of its own allowed-domains list.

// checkOrigin is the gate.
//
// It runs after authentication because the list it checks belongs to the key
// (auth.Identity.AllowedOrigins, filled by whichever authenticator resolved it),
// and before the limiter because a request from an origin the key does not
// cover must not spend the owner's budget: otherwise a scraped key becomes a
// denial of service against the customer it was scraped from.
func (o Options) checkOrigin(ctx context.Context, origin string) *refusal {
	identity, ok := auth.FromContext(ctx)
	if !ok {
		// Only reachable if the gate order were rewired.
		return refuse(code.Code_INTERNAL, messageUnavailable).
			because(errors.New("origin gate ran before authentication"))
	}

	if !identity.MayIngestBrowserTelemetry() {
		// A key that is not a browser key, presented from a web page, means a
		// credential is in a web page. Refusing is the only answer that does not
		// treat a leak as ordinary traffic. This gate judges origins only —
		// what each kind may otherwise do is auth's to say, not the door's.
		if origin != "" {
			return refuse(code.Code_PERMISSION_DENIED, messageNotABrowserKey)
		}
		return nil
	}
	if origin == "" {
		// A browser sends Origin on every POST, same-origin ones included, so a
		// public key arriving without one did not come from a browser.
		return refuse(code.Code_PERMISSION_DENIED, messagePublicKeyWithoutOrigin)
	}
	if !originAllowed(origin, identity.AllowedOrigins) {
		return refuse(code.Code_PERMISSION_DENIED, messageOriginNotAllowed)
	}
	return nil
}

// originNull is what a browser sends from a sandboxed frame, a data: URL or a
// file. It names no site, so it never matches — not even a list that spells it
// out, because a list cannot mean it.
const originNull = "null"

// The two schemes an origin may carry here. http is kept for local development,
// where a customer's first export comes from http://localhost.
const (
	schemeHTTP  = "http"
	schemeHTTPS = "https"
)

// wildcardPrefix covers subdomains of one name: "https://*.example.com" matches
// "https://app.example.com" and, deliberately, not "https://example.com" — a
// customer who listed the subdomains has not listed the apex.
const wildcardPrefix = "*."

// minWildcardLabels is what keeps "https://*.com" from being a list that allows
// the internet. It is a blunt rule and it is not a public-suffix check: nothing
// here stops a customer writing "https://*.co.uk" and harming only themselves.
const minWildcardLabels = 2

func originAllowed(origin string, allowed []string) bool {
	if origin == originNull {
		return false
	}
	presented, ok := parseOrigin(origin)
	if !ok {
		return false
	}
	for _, entry := range allowed {
		if matchesOrigin(presented, entry) {
			return true
		}
	}
	return false
}

// webOrigin is one serialized origin, compared the way RFC 6454 compares them:
// scheme, host and port, with case and a default port carrying no meaning.
type webOrigin struct {
	scheme string
	host   string
	// port is empty when the scheme's own default, so https://x and
	// https://x:443 are the one origin they are.
	port string
}

// parseOrigin normalizes one origin, refusing anything that is not one. A
// stored entry carrying a path or credentials is not an origin, and matching it
// loosely is how an allowlist stops meaning anything.
func parseOrigin(value string) (webOrigin, bool) {
	trimmed := strings.TrimSuffix(strings.TrimSpace(value), "/")
	parsed, err := url.Parse(trimmed)
	if err != nil {
		return webOrigin{}, false
	}

	scheme := strings.ToLower(parsed.Scheme)
	if scheme != schemeHTTP && scheme != schemeHTTPS {
		return webOrigin{}, false
	}
	host := strings.ToLower(parsed.Hostname())
	if host == "" {
		return webOrigin{}, false
	}
	if parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.User != nil {
		return webOrigin{}, false
	}

	port := parsed.Port()
	if port == defaultPortFor(scheme) {
		port = ""
	}
	return webOrigin{scheme: scheme, host: host, port: port}, true
}

func defaultPortFor(scheme string) string {
	if scheme == schemeHTTPS {
		return "443"
	}
	return "80"
}

// matchesOrigin reads one list entry and answers whether it covers the origin
// presented. An entry that is not a legal origin matches nothing, so a typo in
// the list fails closed.
func matchesOrigin(presented webOrigin, entry string) bool {
	pattern, subdomains, ok := parseOriginEntry(entry)
	if !ok {
		return false
	}
	if presented.scheme != pattern.scheme || presented.port != pattern.port {
		return false
	}
	if subdomains {
		return strings.HasSuffix(presented.host, "."+pattern.host)
	}
	return presented.host == pattern.host
}

// parseOriginEntry reads a list entry, reporting whether it covers subdomains.
// The wildcard is stripped before parsing: "*" is not a legal host character and
// a URL parser's treatment of it is not something to depend on.
func parseOriginEntry(entry string) (pattern webOrigin, subdomains bool, ok bool) {
	trimmed := strings.TrimSpace(entry)
	if scheme, rest, found := strings.Cut(trimmed, "://"); found &&
		strings.HasPrefix(rest, wildcardPrefix) {
		subdomains = true
		trimmed = scheme + "://" + strings.TrimPrefix(rest, wildcardPrefix)
	}

	pattern, ok = parseOrigin(trimmed)
	if !ok {
		return webOrigin{}, false, false
	}
	if subdomains && labelCount(pattern.host) < minWildcardLabels {
		return webOrigin{}, false, false
	}
	return pattern, subdomains, true
}

func labelCount(host string) int { return strings.Count(host, ".") + 1 }
