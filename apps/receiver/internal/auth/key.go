package auth

import (
	"crypto/sha256"
	"encoding/hex"
)

// The ingest key format:
//
//	nvk_live_<22 chars base62>
//	└──┬───┘ └────────┬───────┘
//	 scheme        secret (shown once, never stored)
const (
	keyScheme       = "nvk_live_"
	keySecretLength = 22

	// LookupPrefixLength is the plaintext half the api_keys table indexes: the
	// nine-character scheme plus seven characters of the secret. It must stay
	// equal to API_KEY_PREFIX_LENGTH in the control plane's schema — the lookup
	// is an exact match, so a disagreement finds nothing and every key fails.
	LookupPrefixLength = 16
)

// The public site key format, one letter apart from the secret one so a glance
// tells them apart and a scanner can be taught which is which:
//
//	nvp_live_<22 chars base62>
//
// The whole value is the lookup: nothing is withheld, because nothing is secret.
// SiteKeyLength must stay equal to SITE_KEY_LENGTH in the control plane's schema.
const (
	siteKeyScheme       = "nvp_live_"
	siteKeyRandomLength = 22

	SiteKeyLength = len(siteKeyScheme) + siteKeyRandomLength
)

// keyLength is the total width a presented key must have.
const keyLength = len(keyScheme) + keySecretLength

// checkFormat gates the *server* door. The scheme test is what makes a site key
// pasted into a server exporter fail here, before any lookup runs.
func checkFormat(key string) error {
	return checkSchemeAndBase62(key, keyScheme, keyLength)
}

// checkSiteKeyFormat gates the *browser* door, and rejects a secret key for the
// mirror-image reason: a customer must never be able to make `nvk_live_…` work
// from a page, because it would then live in one.
func checkSiteKeyFormat(key string) error {
	return checkSchemeAndBase62(key, siteKeyScheme, SiteKeyLength)
}

func checkSchemeAndBase62(key, scheme string, want int) error {
	if len(key) != want || key[:len(scheme)] != scheme {
		return ErrMalformedKey
	}
	for i := len(scheme); i < len(key); i++ {
		if !isBase62(key[i]) {
			return ErrMalformedKey
		}
	}
	return nil
}

func isBase62(c byte) bool {
	return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
}

// lookupPrefix is only ever called on a key that passed checkFormat.
func lookupPrefix(key string) string { return key[:LookupPrefixLength] }

// HashKey is SHA-256, never argon2: this runs on every ingest request, and a
// high-entropy random key gains nothing from stretching. The control plane
// stores the same lower-case hex digest.
func HashKey(key string) string {
	sum := sha256.Sum256([]byte(key))
	return hex.EncodeToString(sum[:])
}
