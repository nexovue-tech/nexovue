package mask

import "strings"

// The credential in `Authorization: Bearer <token>` is the commonest secret
// found in a log line — a client logs the request it is about to make, or an
// error handler prints the headers it had.
//
// Matching is anchored on the scheme word rather than on what the value looks
// like. That is the whole reason this rule can be on by default: a run of
// characters after `Bearer ` is a credential by definition, so there is no
// judgement to get wrong and no entropy heuristic to tune. A generic
// "high-entropy string" rule was rejected — it would fire on trace ids, build
// hashes and base64 payloads, and ADR 0059's bar is that a developer could
// never legitimately need the masked value to debug.
const (
	// schemeBearer and schemeBasic are the two RFC 7235 schemes that carry the
	// credential inline. Digest and Negotiate carry a challenge, not a secret.
	schemeBearer = "bearer "
	schemeBasic  = "basic "

	// minCredentialLength keeps the rule off short words that follow the scheme
	// in prose — "bearer token missing", "basic auth failed". Real tokens are
	// far longer; twenty is the shortest credential worth calling one, and is
	// what published detection patterns settle on.
	minCredentialLength = 20
)

// maskAuthorizationCredentials replaces the credential after an authorization
// scheme wherever it appears in free text, and reports whether anything changed.
//
// It scans case-insensitively for the scheme, then takes the token characters
// that follow. Everything outside the credential is preserved, so the line
// still reads as what it was — the point is to keep the log useful while making
// the secret useless.
func maskAuthorizationCredentials(text string, replace func(run string) (string, bool)) (string, bool) {
	if len(text) < len(schemeBearer)+minCredentialLength {
		return text, false
	}
	lowered := strings.ToLower(text)

	var built strings.Builder
	changed := false
	cursor := 0

	for cursor < len(text) {
		start, scheme := nextScheme(lowered, cursor)
		if start < 0 {
			break
		}
		credentialAt := start + len(scheme)
		end := credentialEnd(text, credentialAt)
		if end-credentialAt < minCredentialLength {
			// Not a credential — the scheme word appearing in prose. Step past
			// the word itself so the scan cannot stall on it.
			built.WriteString(text[cursor:credentialAt])
			cursor = credentialAt
			continue
		}

		replaced, did := replace(text[credentialAt:end])
		built.WriteString(text[cursor:credentialAt])
		built.WriteString(replaced)
		changed = changed || did
		cursor = end
	}

	if !changed {
		return text, false
	}
	built.WriteString(text[cursor:])
	return built.String(), true
}

// nextScheme finds the earliest authorization scheme at or after from,
// returning where it starts and which one it was.
func nextScheme(lowered string, from int) (start int, scheme string) {
	best := -1
	found := ""
	for _, candidate := range []string{schemeBearer, schemeBasic} {
		at := strings.Index(lowered[from:], candidate)
		if at < 0 {
			continue
		}
		at += from
		if best < 0 || at < best {
			best, found = at, candidate
		}
	}
	return best, found
}

// credentialEnd is where the token after a scheme stops.
//
// The character set is RFC 7235's token68 plus the padding `=`, which covers
// JWTs, opaque bearer tokens and base64 basic credentials. A quote, comma or
// space ends it, so a token inside a JSON document or a quoted header value
// masks without eating what follows.
func credentialEnd(text string, from int) int {
	end := from
	for end < len(text) && isCredentialByte(text[end]) {
		end++
	}
	return end
}

func isCredentialByte(c byte) bool {
	switch {
	case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
		return true
	case c == '-', c == '.', c == '_', c == '~', c == '+', c == '/', c == '=':
		return true
	default:
		return false
	}
}
