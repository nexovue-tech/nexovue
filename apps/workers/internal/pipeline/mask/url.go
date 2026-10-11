package mask

import (
	"regexp"
	"strings"
)

// A credential inside a URL (group 9, B1). The OpenTelemetry conventions make it
// a MUST that `url.full` carries no `user:password@`, and name the presigned-URL
// signatures whose values are sensitive. Neither is ever needed to debug, which
// is this package's bar for a default — and neither is seen by the key rules,
// because the URL sits under an ordinary key such as `url.full` or `http.url`.
//
// The key and the rest of the URL stay, so the request still reads as what it was.

// SensitiveQueryParameters is the conventions' list, matched case-sensitively as
// specified. The browser SDK's `pagecontext.ts` and the scheduler's
// `probes/urls.py` hold the same list, and `test_cross_service_contracts.py`
// asserts the three equal.
var SensitiveQueryParameters = []string{
	"X-Amz-Credential",
	"X-Amz-Security-Token",
	"X-Amz-Signature",
	"X-Goog-Signature",
	"sig",
}

var (
	// `scheme://user:password@` — the password, not the user, is the secret.
	passwordInURL = regexp.MustCompile(`([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)([^\s/@]+)@`)
	// `?name=value` or `&name=value` for a listed name; the value runs to the next
	// separator.
	sensitiveParameter = regexp.MustCompile(
		`([?&#](?:` + alternation(SensitiveQueryParameters) + `)=)([^&#\s"'<>]+)`)
)

func alternation(names []string) string {
	quoted := ""
	for i, name := range names {
		if i > 0 {
			quoted += "|"
		}
		quoted += regexp.QuoteMeta(name)
	}
	return quoted
}

// maskURLCredentials replaces every URL password and listed parameter value in
// text, reporting whether anything changed.
func maskURLCredentials(text string, replace func(run string) (string, bool)) (string, bool) {
	// Every string value of every span passes here; most hold no URL at all,
	// and two substring checks spare them both patterns.
	if !strings.Contains(text, "://") && !strings.ContainsRune(text, '?') {
		return text, false
	}
	changed := false
	swap := func(pattern *regexp.Regexp, in string) string {
		return pattern.ReplaceAllStringFunc(in, func(match string) string {
			parts := pattern.FindStringSubmatch(match)
			kept, secret := parts[1], parts[2]
			replaced, did := replace(secret)
			if !did {
				return match
			}
			changed = true
			if pattern == passwordInURL {
				return kept + replaced + "@"
			}
			return kept + replaced
		})
	}
	text = swap(passwordInURL, text)
	text = swap(sensitiveParameter, text)
	return text, changed
}
