// Package auth turns the ingest credential on a request into the tenancy it
// grants. It is the first gate on the ingest path: nothing downstream of it
// runs for a caller we cannot name.
//
// Verification is a prefix lookup plus a constant-time hash comparison, cached
// in this process. Deliberately not in a shared store: a network hop on the hot
// path costs more than the Postgres lookup it would save, and the set of keys
// one receiver sees is small.
package auth

import (
	"context"
	"crypto/subtle"
	"errors"
	"fmt"
	"time"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/cache"
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

// Kind says which sort of credential granted an identity. It has no safe zero
// value on purpose: an Identity nobody set a kind on is neither kind, and the
// predicates below refuse it.
type Kind string

const (
	// KindSecret is a server-side api_keys credential: hashed, verified in
	// constant time, and the only kind that may use the server ingest doors.
	KindSecret Kind = "secret"

	// KindPublic is a site_keys credential: printed in a customer's page, so it
	// may do one thing — accept browser telemetry for its own project.
	KindPublic Kind = "public"
)

// Identity is the tenancy a valid key grants. Both ids come from one row, so the
// hot path never walks a key to project to organization chain.
type Identity struct {
	// Kind is what the holder of this key is allowed to be. Set by whichever
	// authenticator resolved it, never by a caller.
	Kind Kind

	OrgID     string
	ProjectID string

	// KeyPrefix names the credential rather than the tenant, and is what the
	// rate limiter counts against. For a secret key it is the plaintext half the
	// control plane stores in the clear — an identifier, not a secret, but never
	// logged because three of its characters are secret. For a site key it is
	// the whole public key, which is not a secret at all.
	KeyPrefix string

	// AllowedOrigins is the browser door's origin allow-list, and is empty for
	// every secret credential. Empty means allow nothing: a public key with no
	// origins accepts no browser request.
	//
	// It is abuse mitigation, not an authentication boundary — a caller that
	// sends no Origin header is not filtered by one, which is why the kind's
	// permissions are narrow rather than its origins being trusted.
	AllowedOrigins []string
}

// MayIngestServerTelemetry reports whether this identity may use the server OTLP
// doors. Only the secret kind may. Written as an allow-list so an unset or
// future kind is refused rather than admitted by a missing case.
func (i Identity) MayIngestServerTelemetry() bool { return i.Kind == KindSecret }

// MayIngestBrowserTelemetry reports whether this identity may use the browser
// door. Only the public kind may — a secret key does not belong in a page, and
// accepting one there would teach customers to put it in one.
func (i Identity) MayIngestBrowserTelemetry() bool { return i.Kind == KindPublic }

// Options tune the in-process credential cache, and say where a key's last use
// is recorded.
type Options struct {
	CacheTTL        time.Duration
	CacheMaxEntries int
	// MaxStale is how long past its expiry a key seen valid keeps working while
	// the control plane cannot answer (group 9, C1). Zero refuses at once.
	MaxStale time.Duration

	// UseRecorder is told when a key authenticated against a fresh read. Nil
	// means uses are not recorded, which is what the tests that do not care use.
	UseRecorder KeyUseRecorder
}

// Authenticator verifies presented keys against the control plane.
type Authenticator struct {
	keys  KeyReader
	cache *cache.Cache[Credential]
	uses  KeyUseRecorder
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
	uses := opts.UseRecorder
	if uses == nil {
		uses = noKeyUseRecorder{}
	}
	return &Authenticator{
		keys: keys,
		cache: cache.New(
			opts.CacheTTL, opts.CacheMaxEntries, cache.WithMaxStale[Credential](opts.MaxStale)),
		uses: uses,
	}
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
	stored, cached := a.cache.Lookup(prefix)
	if !cached {
		var err error
		stored, err = a.keys.CredentialFor(ctx, prefix)
		if err != nil {
			// The control plane is down. A key this process saw valid moments ago
			// keeps working for the stale window, so a short Postgres outage is
			// not an ingest outage; the hash below still has to match.
			held, ok := a.cache.Stale(prefix)
			if !ok {
				return Identity{}, fmt.Errorf("looking up ingest key: %w", err)
			}
			StaleServes.Inc()
			stored, cached = held, true
		} else {
			// Misses are cached too, so an invalid key cannot hammer Postgres.
			a.cache.Store(prefix, stored)
		}
	}

	if !stored.Found {
		return Identity{}, ErrUnknownKey
	}
	// Constant time: a byte-by-byte comparison leaks how much of a guessed key
	// was right, which is enough to reconstruct one hash byte at a time.
	if subtle.ConstantTimeCompare([]byte(HashKey(presented)), []byte(stored.KeyHash)) != 1 {
		return Identity{}, ErrUnknownKey
	}

	// Only on a fresh read, and only once the hash matched. That makes the cache
	// TTL the throttle — a cached hit writes nothing — and keeps a guessed prefix
	// from making a key look busy. See lastused.go.
	if !cached {
		a.uses.Record(prefix, time.Now())
	}

	identity := stored.Identity
	identity.Kind = KindSecret
	identity.KeyPrefix = prefix
	return identity, nil
}

// Verdict is what Screen can say about a key from what this process already
// holds.
type Verdict int

const (
	// VerdictRefused means missing, malformed, or a key the cache already knows
	// is unknown, revoked, or carries the wrong secret.
	VerdictRefused Verdict = iota
	// VerdictUnseen means well-formed and not in the cache. Only Authenticate
	// can answer it.
	VerdictUnseen
	// VerdictKnown means the cache holds a live row whose hash this key matches.
	VerdictKnown
)

// Screen judges a key without leaving the process: a format check, a cache
// read and a hash comparison, and never the KeyReader.
//
// It exists for the gRPC door's tap handle, which runs before a request body is
// read and must not block. It is a pre-check, not the gate: every admitted
// request is still authenticated in full.
//
// Expired entries count, asymmetrically. A key that matched a live row is still
// known after its entry expires, or every customer's steady traffic would be
// treated as a stranger's every thirty seconds. A miss is believed only while
// it is fresh: a stale one would refuse a key created since — and refuse it
// forever, because a refused request never reaches Authenticate to refresh it.
func (a *Authenticator) Screen(presented string) Verdict {
	if presented == "" || checkFormat(presented) != nil {
		return VerdictRefused
	}
	stored, fresh, held := a.cache.Peek(lookupPrefix(presented))
	switch {
	case !held:
		return VerdictUnseen
	case !stored.Found:
		if fresh {
			return VerdictRefused
		}
		return VerdictUnseen
	case subtle.ConstantTimeCompare([]byte(HashKey(presented)), []byte(stored.KeyHash)) != 1:
		return VerdictRefused
	default:
		return VerdictKnown
	}
}

// IsCallerFault reports whether err means the caller presented a bad key, as
// opposed to the control plane being unreachable.
func IsCallerFault(err error) bool {
	return errors.Is(err, ErrMissingKey) ||
		errors.Is(err, ErrMalformedKey) ||
		errors.Is(err, ErrUnknownKey)
}
