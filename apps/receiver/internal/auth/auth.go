// Package auth turns the ingest credential on a request into the tenancy it
// grants. It is the first gate on the ingest path: nothing downstream of it
// runs for a caller we cannot name.
//
// Verification is a prefix lookup plus a constant-time hash comparison, cached
// in this process. The cache is deliberately not in Valkey: a network hop on
// the hot path costs more than the Postgres lookup it would save, and the set
// of keys one receiver sees is small.
package auth

import (
	"context"
	"crypto/subtle"
	"errors"
	"fmt"
	"time"
)

// HeaderName carries the ingest key. Lower-case because that is how it is
// documented to customers; Go canonicalises it on the way in either way.
const HeaderName = "x-nexovue-key"

// Why a request was refused. The transport maps all three to 401 — telling an
// unauthenticated caller which of them applied only helps an attacker.
var (
	ErrMissingKey   = errors.New("no ingest key presented")
	ErrMalformedKey = errors.New("ingest key is not in the expected format")
	ErrUnknownKey   = errors.New("ingest key is unknown or revoked")
)

// Identity is the tenancy a valid key grants. Both ids come from one api_keys
// row, so the hot path never walks a key to project to organization chain.
type Identity struct {
	OrgID     string
	ProjectID string

	// KeyPrefix names the credential rather than the tenant, and is what the
	// rate limiter counts against. It is the plaintext half the control plane
	// already stores in the clear, so it is an identifier, not a secret — but
	// it is never logged, because three of its characters are secret.
	KeyPrefix string
}

// Options tune the in-process credential cache.
type Options struct {
	CacheTTL        time.Duration
	CacheMaxEntries int
}

// Authenticator verifies presented keys against the control plane.
type Authenticator struct {
	keys  KeyReader
	cache *cache
}

// KeyReader is the control-plane lookup. An interface because the store behind
// it is a detail of deployment, and because the cache and verification logic
// have to be testable without a Postgres instance.
type KeyReader interface {
	// CredentialFor returns the api_keys row a lookup prefix names, or a
	// Credential with Found false when there is no live row for it.
	CredentialFor(ctx context.Context, lookupPrefix string) (Credential, error)
}

// New builds an authenticator over an api_keys reader.
func New(keys KeyReader, opts Options) *Authenticator {
	return &Authenticator{keys: keys, cache: newCache(opts.CacheTTL, opts.CacheMaxEntries)}
}

// Authenticate resolves a presented key to the tenancy it grants.
//
// A returned ErrMissingKey, ErrMalformedKey or ErrUnknownKey is the caller's
// fault; any other error means we could not reach the control plane and the
// caller should retry.
func (a *Authenticator) Authenticate(ctx context.Context, presented string) (Identity, error) {
	if presented == "" {
		return Identity{}, ErrMissingKey
	}
	if err := checkFormat(presented); err != nil {
		return Identity{}, err
	}

	prefix := lookupPrefix(presented)
	stored, cached := a.cache.lookup(prefix)
	if !cached {
		var err error
		stored, err = a.keys.CredentialFor(ctx, prefix)
		if err != nil {
			return Identity{}, fmt.Errorf("looking up ingest key: %w", err)
		}
		// Misses are cached too, so an invalid key cannot hammer Postgres.
		a.cache.store(prefix, stored)
	}

	if !stored.Found {
		return Identity{}, ErrUnknownKey
	}
	// Constant time: a byte-by-byte comparison leaks how much of a guessed key
	// was right, which is enough to reconstruct one hash byte at a time.
	if subtle.ConstantTimeCompare([]byte(HashKey(presented)), []byte(stored.KeyHash)) != 1 {
		return Identity{}, ErrUnknownKey
	}

	identity := stored.Identity
	identity.KeyPrefix = prefix
	return identity, nil
}

// IsCallerFault reports whether err means the caller presented a bad key, as
// opposed to the control plane being unreachable.
func IsCallerFault(err error) bool {
	return errors.Is(err, ErrMissingKey) ||
		errors.Is(err, ErrMalformedKey) ||
		errors.Is(err, ErrUnknownKey)
}
