package auth

import (
	"context"
	"fmt"
	"time"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/cache"
)

// The browser door's half of authentication.
//
// A site key is public: it is printed in the page that carries it, so anyone who
// views source holds it. Three things follow, and they are the whole design.
//
// **There is no hash and no constant-time comparison.** There is no secret half
// to withhold, so the control plane stores the value whole and the lookup itself
// is the verification. The secret door's `subtle.ConstantTimeCompare` exists to
// stop a guessed key being reconstructed one hash byte at a time; here there is
// nothing to reconstruct, because the value is already published. Removing a
// comparison from a credential path deserves suspicion, so it is stated: the
// thing the comparison protected does not exist on this path.
//
// **The lookup reads site_keys and nothing else.** SiteAuthenticator is a
// separate type over a separate reader over a separate table, so no arrangement
// of this code can answer a browser request with a secret key's tenancy or a
// server request with a public key's. That is the guarantee a `kind` column on
// one table could not have given.
//
// **What it grants is narrow, and named rather than assumed.** The identity it
// returns carries KindPublic, so MayIngestServerTelemetry is false on it: it may
// accept browser telemetry for its own project and reach nothing else — no other
// project, no read of stored telemetry, no control-plane action.

// SiteKeyReader resolves a public key to the tenancy it grants. Separate from
// KeyReader because it reads a different table and returns a credential with no
// hash in it; one interface over both would be one place to confuse them.
type SiteKeyReader interface {
	// SiteCredentialFor returns the live site_keys row this public key names, or
	// a SiteCredential with Found false when there is none.
	SiteCredentialFor(ctx context.Context, publicKey string) (SiteCredential, error)
}

// SiteAuthenticator verifies public browser keys against the control plane.
type SiteAuthenticator struct {
	keys  SiteKeyReader
	cache *cache.Cache[SiteCredential]
	uses  KeyUseRecorder
}

// NewSite builds an authenticator over a site_keys reader. Options mean the same
// as they do for the secret door, so both doors age at the same rate: a revoked
// site key stops working within one CacheTTL, exactly as a revoked secret one does.
func NewSite(keys SiteKeyReader, opts Options) *SiteAuthenticator {
	uses := opts.UseRecorder
	if uses == nil {
		uses = noKeyUseRecorder{}
	}
	return &SiteAuthenticator{
		keys: keys,
		cache: cache.New(
			opts.CacheTTL, opts.CacheMaxEntries, cache.WithMaxStale[SiteCredential](opts.MaxStale)),
		uses: uses,
	}
}

// Authenticate resolves a presented public key to the tenancy it grants.
//
// The returned Identity carries KindPublic and the key's allowed origins. It does
// not enforce those origins: matching a request's Origin header is the transport's
// job, because only the transport has the header. An empty list means allow
// nothing.
//
// Errors mean what they do on the secret door: ErrMissingKey, ErrMalformedKey and
// ErrUnknownKey are the caller's fault, anything else is ours and is retryable.
func (a *SiteAuthenticator) Authenticate(ctx context.Context, presented string) (Identity, error) {
	if presented == "" {
		return Identity{}, ErrMissingKey
	}
	if err := checkSiteKeyFormat(presented); err != nil {
		return Identity{}, err
	}

	stored, cached := a.cache.Lookup(presented)
	if !cached {
		var err error
		stored, err = a.keys.SiteCredentialFor(ctx, presented)
		if err != nil {
			// The secret door's rule: a key seen valid moments ago rides out a
			// control-plane outage for the stale window (group 9, C1).
			held, ok := a.cache.Stale(presented)
			if !ok {
				return Identity{}, fmt.Errorf("looking up site key: %w", err)
			}
			StaleServes.Inc()
			stored, cached = held, true
		} else {
			// Misses cache too, so a bad key in a page cannot hammer Postgres — and
			// a bad key in a page is a request per pageview, not per deploy.
			a.cache.Store(presented, stored)
		}
	}

	if !stored.Found {
		return Identity{}, ErrUnknownKey
	}

	// Throttled by the cache, exactly as the secret door's is. The secret door
	// also waits for the hash comparison so a guessed prefix cannot make a key
	// look busy; here the match is total, so arriving at this line already means
	// the caller holds the whole key. The reading is weaker for a different
	// reason: the key is public, so a stranger who read the page can make it
	// look busy. That errs towards "still in use", never towards
	// "safe to revoke", so it cannot mislead the decision it informs.
	if !cached {
		a.uses.Record(presented, time.Now())
	}

	identity := stored.Identity
	identity.Kind = KindPublic
	identity.KeyPrefix = presented
	return identity, nil
}
