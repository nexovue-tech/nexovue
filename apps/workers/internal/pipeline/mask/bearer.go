package mask

import "strings"

// The credential in `Authorization: Bearer <token>` is the commonest secret
// found in a log line — a client logs the request it is about to make, or an
// error handler prints the headers it had.
//
// Two things gate a match, and both exist because the first version of this
// rule had neither and destroyed ordinary data:
//
//   - The scheme must sit on a **word boundary**. Without it `standard-bearer`
//     matched, and everything after it was blanked.
//   - The scheme must sit in an **authorization context** — just after a word
//     beginning `auth`, or as the whole value with nothing trailing. Without it
//     "loaded basic /etc/nexovue/receiver/config.yaml" and "applying basic
//     transform_pipeline_stage_normalize" were both destroyed, because a path
//     and a dotted identifier are made of the same characters a token is.
//
// This is the same correction the card rule received: Luhn alone is not a card
// test, and a scheme word alone is not an authorization header. The earlier
// comment here claimed there was "no judgement to get wrong". There was.
//
// The cost of the context requirement is stated rather than hidden: a token
// mentioned mid-sentence — "using Bearer abc… to call payments" — is **not**
// masked by default. That is the deliberate side to err on, because masking is
// irreversible and this record's bar is that a default must never destroy
// something a developer could legitimately need. A project that logs
// credentials that way can name the attribute in its own key rules.
const (
	// schemeBearer and schemeBasic are the two RFC 7235 schemes that carry the
	// credential inline. Digest and Negotiate carry a challenge, not a secret.
	schemeBearer = "bearer "
	schemeBasic  = "basic "

	// authorizationMarker is what puts a scheme in a credentials position when
	// it is not at the start of the value.
	//
	// The stem, not the whole word. Matching `authorization` exactly missed
	// `POST /authorize Bearer <token>` by four letters, and a real credential
	// was written to storage in the clear as a result. `auth` covers the header
	// however it is spelled, the route that handles it, and `auth:`.
	//
	// It also admits `author` and `authority`. That is the accepted cost: a word
	// beginning with auth, immediately before a bearer scheme and a token-shaped
	// run, is a credential far more often than it is prose.
	authorizationMarker = "auth"

	// authorizationReach is how far after that marker a scheme still counts as
	// being in its context. Enough for `Authorization: `, `"authorization":"`
	// and similar punctuation, and short enough that an unrelated later mention
	// of a scheme does not inherit it.
	authorizationReach = 24

	// minCredentialLength keeps the rule off short words that follow a scheme in
	// prose. Real tokens are far longer; published detection patterns settle on
	// the same floor.
	minCredentialLength = 20
)

// maskAuthorizationCredentials replaces the credential after an authorization
// scheme, and reports whether anything changed. Everything outside the
// credential is preserved, so the line still reads as what it was.
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

		if end-credentialAt < minCredentialLength || !inAuthorizationContext(lowered, start, end) {
			// Prose that happens to contain the word, or a run too short to be
			// a credential. Step past the scheme so the scan cannot stall.
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

// nextScheme finds the earliest scheme at or after from that begins a word.
func nextScheme(lowered string, from int) (start int, scheme string) {
	best, found := -1, ""
	for _, candidate := range []string{schemeBearer, schemeBasic} {
		at := from
		for {
			offset := strings.Index(lowered[at:], candidate)
			if offset < 0 {
				break
			}
			at += offset
			if startsAWord(lowered, at) {
				break
			}
			// `standard-bearer` and the like: keep looking past this one.
			at += len(candidate)
		}
		if at >= len(lowered) || strings.Index(lowered[at:], candidate) != 0 {
			continue
		}
		if best < 0 || at < best {
			best, found = at, candidate
		}
	}
	return best, found
}

// startsAWord reports whether position at begins a word rather than ending one.
func startsAWord(text string, at int) bool {
	if at == 0 {
		return true
	}
	return !isWordByte(text[at-1])
}

func isWordByte(c byte) bool {
	switch {
	case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
		return true
	case c == '-', c == '_':
		return true
	default:
		return false
	}
}

// inAuthorizationContext reports whether a scheme at start is being used as one.
//
// Two ways to qualify. Either the word `authorization` sits just before it — the
// header, however it was rendered — or the value *is* the credential and nothing
// else, which is what an Authorization header's value looks like on its own.
//
// "Value and nothing else" is deliberately strict: a message merely beginning
// with the word, like "basic auth.middleware.verify_request_credentials
// failed", is prose, and trailing words are what give it away.
func inAuthorizationContext(lowered string, start, credentialEnd int) bool {
	from := start - authorizationReach
	if from < 0 {
		from = 0
	}
	if strings.Contains(lowered[from:start], authorizationMarker) {
		return true
	}
	leading := strings.TrimLeft(lowered[:start], " \t\"'")
	trailing := strings.TrimRight(lowered[credentialEnd:], " \t\"',}")
	return leading == "" && trailing == ""
}

// credentialEnd is where the token after a scheme stops.
//
// The character set is RFC 7235's token68 plus the padding `=`, which covers
// JWTs, opaque bearer tokens and base64 basic credentials. A quote, comma or
// space ends it, so a token inside a JSON document masks without eating what
// follows.
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
