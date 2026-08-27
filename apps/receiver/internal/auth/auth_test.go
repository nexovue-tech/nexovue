package auth

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
	"testing"
	"time"
)

const (
	validKey  = "nvk_live_devlocaldevlocaldevloc"
	orgID     = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
	projectID = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
)

func digestOf(key string) string {
	sum := sha256.Sum256([]byte(key))
	return hex.EncodeToString(sum[:])
}

// fakeKeys stands in for the api_keys table and counts how often it is read, so
// a test can prove the cache is doing its job.
type fakeKeys struct {
	rows  map[string]Credential
	err   error
	reads int
}

func (f *fakeKeys) CredentialFor(_ context.Context, prefix string) (Credential, error) {
	f.reads++
	if f.err != nil {
		return Credential{}, f.err
	}
	if row, ok := f.rows[prefix]; ok {
		return row, nil
	}
	return Credential{Found: false}, nil
}

func seededKeys(key string) *fakeKeys {
	return &fakeKeys{rows: map[string]Credential{
		key[:LookupPrefixLength]: {
			Identity: Identity{OrgID: orgID, ProjectID: projectID},
			KeyHash:  digestOf(key),
			Found:    true,
		},
	}}
}

func newTestAuthenticator(keys KeyReader) *Authenticator {
	return New(keys, Options{CacheTTL: time.Minute, CacheMaxEntries: 100})
}

func TestAuthenticateResolvesBothIdsFromOneRow(t *testing.T) {
	auth := newTestAuthenticator(seededKeys(validKey))

	identity, err := auth.Authenticate(context.Background(), validKey)
	if err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}
	if identity.OrgID != orgID {
		t.Errorf("OrgID = %q, want %q", identity.OrgID, orgID)
	}
	if identity.ProjectID != projectID {
		t.Errorf("ProjectID = %q, want %q", identity.ProjectID, projectID)
	}
	if identity.KeyPrefix != validKey[:LookupPrefixLength] {
		t.Errorf("KeyPrefix = %q, want the lookup prefix", identity.KeyPrefix)
	}
}

func TestAuthenticateRefusesUnusableKeys(t *testing.T) {
	cases := map[string]struct {
		key  string
		want error
	}{
		"absent":            {"", ErrMissingKey},
		"wrong scheme":      {"nvk_test_devlocaldevlocaldevloc", ErrMalformedKey},
		"too short":         {"nvk_live_devlocal", ErrMalformedKey},
		"too long":          {validKey + "x", ErrMalformedKey},
		"non-base62 secret": {"nvk_live_devlocaldevlocaldevlo-", ErrMalformedKey},
		"unknown prefix":    {"nvk_live_absentabsentabsentabse", ErrUnknownKey},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			auth := newTestAuthenticator(seededKeys(validKey))

			_, err := auth.Authenticate(context.Background(), tc.key)
			if !errors.Is(err, tc.want) {
				t.Fatalf("Authenticate() = %v, want %v", err, tc.want)
			}
			if !IsCallerFault(err) {
				t.Error("a bad key is the caller's fault, not a dependency failure")
			}
		})
	}
}

// A key whose prefix exists but whose secret does not must be refused: the
// prefix is a lookup handle, and the hash is the only thing that authenticates.
func TestAuthenticateRefusesTheRightPrefixWithTheWrongSecret(t *testing.T) {
	keys := seededKeys(validKey)
	impostor := validKey[:LookupPrefixLength] + strings.Repeat("z", len(validKey)-LookupPrefixLength)

	auth := newTestAuthenticator(keys)
	if _, err := auth.Authenticate(context.Background(), impostor); !errors.Is(err, ErrUnknownKey) {
		t.Fatalf("Authenticate() = %v, want %v", err, ErrUnknownKey)
	}
}

func TestAuthenticateCachesTheLookup(t *testing.T) {
	keys := seededKeys(validKey)
	auth := newTestAuthenticator(keys)

	for range 5 {
		if _, err := auth.Authenticate(context.Background(), validKey); err != nil {
			t.Fatalf("Authenticate() = %v, want no error", err)
		}
	}
	if keys.reads != 1 {
		t.Errorf("read api_keys %d times, want 1: the hot path must not requery", keys.reads)
	}
}

// Misses cache too, or an invalid key becomes a Postgres query per request.
func TestAuthenticateCachesMisses(t *testing.T) {
	keys := seededKeys(validKey)
	auth := newTestAuthenticator(keys)

	for range 5 {
		if _, err := auth.Authenticate(context.Background(), "nvk_live_absentabsentabsentabse"); !errors.Is(err, ErrUnknownKey) {
			t.Fatalf("Authenticate() = %v, want %v", err, ErrUnknownKey)
		}
	}
	if keys.reads != 1 {
		t.Errorf("read api_keys %d times, want 1: an invalid key must not reach Postgres repeatedly", keys.reads)
	}
}

func TestAuthenticateRequeriesOnceTheCacheEntryExpires(t *testing.T) {
	keys := seededKeys(validKey)
	auth := New(keys, Options{CacheTTL: time.Minute, CacheMaxEntries: 100})

	now := time.Now()
	auth.cache.now = func() time.Time { return now }
	if _, err := auth.Authenticate(context.Background(), validKey); err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}

	// Revocation must take effect within the TTL, so a key is re-read after it.
	now = now.Add(time.Minute + time.Second)
	if _, err := auth.Authenticate(context.Background(), validKey); err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}
	if keys.reads != 2 {
		t.Errorf("read api_keys %d times, want 2: an expired entry must be refreshed", keys.reads)
	}
}

// A control-plane outage is our failure, not the caller's, so the transport can
// tell a retryable 503 from a 401.
func TestAuthenticateReportsALookupFailureSeparately(t *testing.T) {
	unreachable := errors.New("dial tcp: connection refused")
	auth := newTestAuthenticator(&fakeKeys{err: unreachable})

	_, err := auth.Authenticate(context.Background(), validKey)
	if !errors.Is(err, unreachable) {
		t.Fatalf("Authenticate() = %v, want the lookup failure", err)
	}
	if IsCallerFault(err) {
		t.Error("an unreachable control plane must not be reported as a bad key")
	}
}

func TestCacheStaysWithinItsCeiling(t *testing.T) {
	const maxEntries = 8
	cache := newCache(time.Minute, maxEntries)

	for i := range 100 {
		cache.store(string(rune('a'+i%26))+string(rune('a'+i/26)), Credential{Found: false})
		if len(cache.entries) > maxEntries {
			t.Fatalf("cache grew to %d entries, want at most %d: misses are attacker-chosen",
				len(cache.entries), maxEntries)
		}
	}
}
