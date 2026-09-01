package otlp

// Internal, unlike the rest of the door's suite: the origin gate reads an
// identity out of the context, and the only way to hand it a public one today
// is to build it. The door still authenticates every request with the secret
// authenticator, so nothing outside this file can reach the public branch.

import (
	"context"
	"testing"

	"google.golang.org/genproto/googleapis/rpc/code"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
)

func TestOriginMatchingIsExactUnlessTheEntrySaysOtherwise(t *testing.T) {
	cases := []struct {
		name    string
		origin  string
		allowed []string
		want    bool
	}{
		{"exact", "https://shop.example.com", []string{"https://shop.example.com"}, true},
		{"one of several", "https://b.example.com",
			[]string{"https://a.example.com", "https://b.example.com"}, true},
		{"host case is not meaningful", "https://SHOP.example.com",
			[]string{"https://shop.example.com"}, true},
		{"a stored trailing slash still matches", "https://shop.example.com",
			[]string{"https://shop.example.com/"}, true},
		{"the scheme's default port is the same origin", "https://shop.example.com:443",
			[]string{"https://shop.example.com"}, true},
		{"localhost keeps its port", "http://localhost:3000",
			[]string{"http://localhost:3000"}, true},

		{"a different scheme is a different origin", "http://shop.example.com",
			[]string{"https://shop.example.com"}, false},
		{"a different port is a different origin", "http://localhost:3001",
			[]string{"http://localhost:3000"}, false},
		{"a suffix is not a match", "https://evil-example.com",
			[]string{"https://example.com"}, false},
		{"a subdomain is not the apex", "https://evil.example.com",
			[]string{"https://example.com"}, false},
		{"an empty list allows nothing", "https://shop.example.com", nil, false},
		{"null names no site", originNull, []string{originNull, "https://shop.example.com"}, false},
		{"an entry that is not an origin matches nothing", "https://shop.example.com",
			[]string{"https://shop.example.com/collect"}, false},
		{"a non-web scheme is not an origin", "ftp://shop.example.com",
			[]string{"ftp://shop.example.com"}, false},

		{"a wildcard covers subdomains", "https://pr-42.preview.example.com",
			[]string{"https://*.example.com"}, true},
		{"a wildcard is not the apex", "https://example.com",
			[]string{"https://*.example.com"}, false},
		{"a wildcard keeps its scheme", "http://app.example.com",
			[]string{"https://*.example.com"}, false},
		{"a wildcard over a bare tld allows nothing", "https://anything.com",
			[]string{"https://*.com"}, false},
		{"a bare wildcard is not an entry", "https://shop.example.com",
			[]string{"https://*"}, false},
	}

	for _, one := range cases {
		t.Run(one.name, func(t *testing.T) {
			if got := originAllowed(one.origin, one.allowed); got != one.want {
				t.Errorf("originAllowed(%q, %v) = %v, want %v",
					one.origin, one.allowed, got, one.want)
			}
		})
	}
}

// contextFor is a request that has passed authentication as identity.
func contextFor(identity auth.Identity) context.Context {
	return auth.NewContext(context.Background(), identity)
}

func publicKey(origins ...string) auth.Identity {
	return auth.Identity{Kind: auth.KindPublic, AllowedOrigins: origins}
}

func secretKey() auth.Identity {
	return auth.Identity{Kind: auth.KindSecret}
}

// refusedWith fails unless the gate refused for the stated reason.
func refusedWith(t *testing.T, refused *refusal, want code.Code, wantMessage string) {
	t.Helper()
	if refused == nil {
		t.Fatalf("request was allowed, want refused with %s", want)
	}
	if refused.code != want {
		t.Errorf("code = %s, want %s", refused.code, want)
	}
	if refused.message != wantMessage {
		t.Errorf("message = %q, want %q", refused.message, wantMessage)
	}
}

func TestSecretKeyPresentedFromAPageIsRefused(t *testing.T) {
	// The credential is in a web page. Accepting it would make a leak routine.
	refused := Options{}.checkOrigin(contextFor(secretKey()), "https://shop.example.com")

	refusedWith(t, refused, code.Code_PERMISSION_DENIED, messageNotABrowserKey)
}

func TestSecretKeyWithNoOriginIsUnaffected(t *testing.T) {
	// Every server exporter in production is this request. The gate must be
	// invisible to it.
	if refused := (Options{}).checkOrigin(contextFor(secretKey()), ""); refused != nil {
		t.Fatalf("server export was refused %s: %s", refused.code, refused.message)
	}
}

func TestPublicKeyWithoutAnOriginIsRefused(t *testing.T) {
	// A browser sends Origin on every POST, so this one is not a browser — and
	// this is also what a public key presented over gRPC hits.
	refused := Options{}.checkOrigin(contextFor(publicKey("https://shop.example.com")), "")

	refusedWith(t, refused, code.Code_PERMISSION_DENIED, messagePublicKeyWithoutOrigin)
}

func TestPublicKeyFromAListedOriginIsAllowed(t *testing.T) {
	identity := publicKey("https://shop.example.com")

	if refused := (Options{}).checkOrigin(contextFor(identity), "https://shop.example.com"); refused != nil {
		t.Fatalf("listed origin was refused %s: %s", refused.code, refused.message)
	}
}

func TestPublicKeyFromAnUnlistedOriginIsRefused(t *testing.T) {
	// The scraped-key case: someone else's page, holding a key they copied.
	identity := publicKey("https://shop.example.com")

	refused := Options{}.checkOrigin(contextFor(identity), "https://evil.example")

	refusedWith(t, refused, code.Code_PERMISSION_DENIED, messageOriginNotAllowed)
}

func TestOriginGateWithoutAnIdentityIsOurFault(t *testing.T) {
	// Only reachable by rewiring the gate order, and it must not read as the
	// caller's mistake if anyone ever does.
	refused := Options{}.checkOrigin(context.Background(), "https://shop.example.com")

	if refused == nil || refused.code != code.Code_INTERNAL {
		t.Fatalf("refusal = %v, want INTERNAL", refused)
	}
	if refused.cause == nil {
		t.Error("no cause was attached; the log line would say nothing")
	}
}
