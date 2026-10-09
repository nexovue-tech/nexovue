package mask

import "testing"

// The regression: masking is irreversible, and the credential-keys rule is on
// by default over every attribute. A bare substring match on "token" masked
// gen_ai.usage.input_tokens and output_tokens — LLM token *counts*, not
// credentials — before F29's screen could ever read a real number. Found
// seeding fixtures through the real ingest path, not reasoned about: the
// seeded counts came back as the masking placeholder.
//
// `containsSingularToken` takes an already-lowered key, matching its one
// caller (`masksKey` lowers before calling it) — case-insensitivity is that
// caller's job, already covered for a different marker by "uppercase password
// key" in mask_test.go, not re-tested per marker here.
func TestTokenCountsAreNotMistakenForCredentials(t *testing.T) {
	notCredentials := []string{
		"gen_ai.usage.input_tokens",
		"gen_ai.usage.output_tokens",
		"rate_limit.tokens_remaining",
		"max_tokens",
		"tokens",
	}
	for _, key := range notCredentials {
		t.Run(key, func(t *testing.T) {
			if containsSingularToken(key) {
				t.Errorf("%q was matched as a credential key; it is a token count, not a token", key)
			}
		})
	}
}

// The rule still has to do its job: every real shape of a token credential is
// still caught, whatever it is called.
func TestRealTokenCredentialsAreStillMasked(t *testing.T) {
	credentials := []string{
		"token",
		"access_token",
		"session_token",
		"api_token",
		"refresh_token",
		"auth.access_token",
		"token_signature",
		// Unrealistic, but not ruled out: a genuine singular occurrence after a
		// plural one must still be found, not skipped past.
		"tokens_token",
	}
	for _, key := range credentials {
		t.Run(key, func(t *testing.T) {
			if !containsSingularToken(key) {
				t.Errorf("%q is a credential-shaped key and must be masked", key)
			}
		})
	}
}
