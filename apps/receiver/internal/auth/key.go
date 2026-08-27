package auth

import (
	"crypto/sha256"
	"encoding/hex"
)

// The ingest key format, documented in docs/architecture/tenancy-and-data-model.md:
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

// keyLength is the total width a presented key must have.
const keyLength = len(keyScheme) + keySecretLength

func checkFormat(key string) error {
	if len(key) != keyLength || key[:len(keyScheme)] != keyScheme {
		return ErrMalformedKey
	}
	for i := len(keyScheme); i < len(key); i++ {
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
