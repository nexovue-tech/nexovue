package mask

import "testing"

func blankCredential(run string) (string, bool) { return Placeholder, true }

// TestCredentialsAfterAnAuthorizationSchemeAreMasked covers the shapes a token
// actually arrives in — a header line, a curl command echoed into a log, a JSON
// document, and a JWT with its dots and padding.
func TestCredentialsAfterAnAuthorizationSchemeAreMasked(t *testing.T) {
	cases := map[string]struct{ in, want string }{
		"header line": {
			in:   "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
			want: "Authorization: Bearer " + Placeholder,
		},
		"lower case scheme": {
			in:   "authorization: bearer abcdefghijklmnopqrstuvwxyz012345",
			want: "authorization: bearer " + Placeholder,
		},
		"basic credentials": {
			in:   "Basic dXNlcm5hbWU6c3VwZXJzZWNyZXRwYXNzd29yZA==",
			want: "Basic " + Placeholder,
		},
		"inside JSON keeps the quote": {
			in:   `{"authorization":"Bearer abcdefghijklmnopqrstuvwxyz","id":7}`,
			want: `{"authorization":"Bearer ` + Placeholder + `","id":7}`,
		},
		"jwt with dots and padding": {
			in:   "Bearer eyJhbGci.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4=",
			want: "Bearer " + Placeholder,
		},
		"two on one line": {
			in:   "old=Bearer aaaaaaaaaaaaaaaaaaaaaaaa new=Bearer bbbbbbbbbbbbbbbbbbbbbbbb",
			want: "old=Bearer " + Placeholder + " new=Bearer " + Placeholder,
		},
		"trailing text survives": {
			in:   "using Bearer aaaaaaaaaaaaaaaaaaaaaaaa to call payments",
			want: "using Bearer " + Placeholder + " to call payments",
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got, changed := maskAuthorizationCredentials(tc.in, blankCredential)
			if !changed {
				t.Fatalf("nothing masked in %q", tc.in)
			}
			if got != tc.want {
				t.Errorf("got  %q\nwant %q", got, tc.want)
			}
		})
	}
}

// TestOrdinaryTextMentioningASchemeIsLeftAlone is the false-positive guard.
//
// The card rule taught this lesson the hard way: a Luhn-passing nanosecond
// timestamp was masked, and the mangled value broke a chaos run. A rule that
// eats ordinary prose is worse than no rule, because it destroys evidence
// silently.
func TestOrdinaryTextMentioningASchemeIsLeftAlone(t *testing.T) {
	untouched := []string{
		"bearer token missing from request",
		"Basic auth failed for user 42",
		"the bearer of this message is unauthenticated",
		"Bearer",
		"Bearer short",
		"switching to basic authentication",
	}

	for _, text := range untouched {
		t.Run(text, func(t *testing.T) {
			got, changed := maskAuthorizationCredentials(text, blankCredential)
			if changed {
				t.Errorf("masked ordinary text: %q became %q", text, got)
			}
			if got != text {
				t.Errorf("text was rewritten: got %q, want %q", got, text)
			}
		})
	}
}

// TestABearerTokenInALogBodyDoesNotSurviveMasking proves the rule reaches
// through the real entry point, not just its own helper.
func TestABearerTokenInALogBodyDoesNotSurviveMasking(t *testing.T) {
	rules := Defaults()
	token := "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
	masked, changed := rules.maskValue("calling payments with Bearer " + token)

	if !changed {
		t.Fatal("a bearer token in a message was not masked")
	}
	if contains(masked, token) {
		t.Errorf("the token survived: %q", masked)
	}
	if !contains(masked, "calling payments") {
		t.Errorf("the message lost its meaning: %q", masked)
	}
}

// TestTheCardRuleStillAppliesAlongsideCredentials — adding a second value rule
// must not switch the first one off.
func TestTheCardRuleStillAppliesAlongsideCredentials(t *testing.T) {
	rules := Defaults()
	masked, changed := rules.maskValue("card 4111111111111111 and Bearer aaaaaaaaaaaaaaaaaaaaaaaa")
	if !changed {
		t.Fatal("nothing masked")
	}
	if contains(masked, "4111111111111111") {
		t.Errorf("the card survived: %q", masked)
	}
	if contains(masked, "aaaaaaaaaaaaaaaaaaaaaaaa") {
		t.Errorf("the token survived: %q", masked)
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && indexOf(haystack, needle) >= 0
}

func indexOf(haystack, needle string) int {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}
