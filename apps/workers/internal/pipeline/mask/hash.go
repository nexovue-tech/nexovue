package mask

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

// A pseudonym carries the first 128 bits of the digest, hex encoded. Full width
// would double what every hashed attribute costs to store for no gain: at 128
// bits two different subjects colliding — which would wrongly correlate them —
// is not a thing that happens at any volume we will see.
const pseudonymBytes = 16

// A pseudonym stays recognisably masked, so a stored value still says what
// happened to it and a reader never mistakes one for the original.
const (
	pseudonymPrefix = "[masked:"
	pseudonymSuffix = "]"
)

// minHashKeyBytes is the shortest secret HMAC-SHA-256 is accepted with: a key
// shorter than the digest weakens the construction and saves nothing.
const minHashKeyBytes = 32

// HashKey is the secret hashing rules derive pseudonyms with.
//
// It is a type of its own so the secret cannot be passed around, printed or
// logged as an ordinary string by accident.
type HashKey struct{ secret []byte }

// NewHashKey returns a hash key over secret, which must be at least 32 bytes.
//
// The same secret must come back after a restart: a pseudonym is only useful
// while it stays equal for equal inputs over time, and a new key silently ends
// every correlation that spans the change.
func NewHashKey(secret []byte) (HashKey, error) {
	if len(secret) < minHashKeyBytes {
		return HashKey{}, fmt.Errorf(
			"masking hash key is %d bytes, want at least %d", len(secret), minHashKeyBytes)
	}
	return HashKey{secret: append([]byte(nil), secret...)}, nil
}

// DeriveHashKey returns the key for one project, derived from a deployment-wide
// secret.
//
// Per project, not per deployment. One shared key would give a person the same
// pseudonym in every tenant's data, so anyone able to read two tenants could
// join them on it — the tenant-isolation invariant leaking out through a privacy
// feature. Deriving costs a customer correlation across their own projects, and
// a project is an environment, so what is lost is correlating one
// person between staging and production. Nobody needs that.
//
// HMAC as a key-derivation step: the master secret is the key, the project id
// the message, so the result is independent per project and reveals nothing
// about the master.
func DeriveHashKey(master []byte, projectID string) (HashKey, error) {
	if len(master) < minHashKeyBytes {
		return HashKey{}, fmt.Errorf(
			"masking hash secret is %d bytes, want at least %d", len(master), minHashKeyBytes)
	}
	mac := hmac.New(sha256.New, master)
	mac.Write([]byte("nexovue/masking/v1/project/" + projectID))
	return HashKey{secret: mac.Sum(nil)}, nil
}

// String hides the secret from every log line and error that formats a value.
func (k HashKey) String() string { return "[masking hash key]" }

func (k HashKey) configured() bool { return len(k.secret) > 0 }

// pseudonym is a truncated HMAC-SHA-256 of value.
//
// Keyed, because a plain digest of a low-cardinality value — an email, an
// account id, a card number — is recovered by hashing candidates until one
// matches. The key is what makes that attack need the key first.
func (k HashKey) pseudonym(value string) string {
	mac := hmac.New(sha256.New, k.secret)
	mac.Write([]byte(value)) // hash.Hash documents that Write never fails.
	return pseudonymPrefix + hex.EncodeToString(mac.Sum(nil)[:pseudonymBytes]) + pseudonymSuffix
}
