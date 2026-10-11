package mask

import "testing"

// Group 9, B1: a credential inside a URL is never something a developer debugs
// with. The OpenTelemetry conventions make it a MUST that `url.full` carries no
// `user:password@`, and name the presigned-URL signatures as sensitive.
func TestCredentialsInsideAURLAreMasked(t *testing.T) {
	rules := Defaults()
	cases := map[string]string{
		"https://deploy:hunter2@db.example.com:5432/app":                                         "https://deploy:[masked]@db.example.com:5432/app",
		"https://bucket.s3.amazonaws.com/k?X-Amz-Credential=AKIA%2F1&X-Amz-Signature=abc123&x=1": "https://bucket.s3.amazonaws.com/k?X-Amz-Credential=[masked]&X-Amz-Signature=[masked]&x=1",
		"GET https://storage.googleapis.com/b/o?X-Goog-Signature=deadbeef failed":                "GET https://storage.googleapis.com/b/o?X-Goog-Signature=[masked] failed",
		"https://acct.blob.core.windows.net/c/b?sv=2022&sig=Zm9v%3D":                             "https://acct.blob.core.windows.net/c/b?sv=2022&sig=[masked]",
	}
	for input, want := range cases {
		got, changed := rules.maskValue(input)
		if !changed || got != want {
			t.Errorf("maskValue(%q)\n got  %q\n want %q", input, got, want)
		}
	}
}

func TestOrdinaryURLsAreLeftAlone(t *testing.T) {
	rules := Defaults()
	for _, input := range []string{
		"https://api.example.com/v1/orders?page=2&sort=asc",
		"https://user@example.com/no-password-here",
		"https://example.com/signature?signed=yes",
		"mailto:someone@example.com",
		"https://example.com/?x-amz-signature=lowercase-is-not-the-specified-name",
	} {
		if got, changed := rules.maskValue(input); changed {
			t.Errorf("maskValue(%q) changed it to %q", input, got)
		}
	}
}

func TestTheURLFamilyCanBeSwitchedOff(t *testing.T) {
	rules, err := RulesFromJSON([]byte(`{"disable": ["url_credentials"]}`))
	if err != nil {
		t.Fatal(err)
	}
	input := "https://deploy:hunter2@db.example.com/app"
	if got, changed := rules.maskValue(input); changed {
		t.Fatalf("a disabled family still masked: %q", got)
	}
}
