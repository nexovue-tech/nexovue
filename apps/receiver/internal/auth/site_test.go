package auth

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

const validSiteKey = "nvp_live_devlocaldevlocaldevloc"

// fakeSiteKeys stands in for the site_keys table and counts reads, so a test can
// prove the cache is doing its job.
type fakeSiteKeys struct {
	rows  map[string]SiteCredential
	err   error
	reads int
}

func (f *fakeSiteKeys) SiteCredentialFor(_ context.Context, key string) (SiteCredential, error) {
	f.reads++
	if f.err != nil {
		return SiteCredential{}, f.err
	}
	if row, ok := f.rows[key]; ok {
		return row, nil
	}
	return SiteCredential{Found: false}, nil
}

func seededSiteKeys(key string, origins ...string) *fakeSiteKeys {
	return &fakeSiteKeys{rows: map[string]SiteCredential{
		key: {
			Identity: Identity{
				OrgID:          orgID,
				ProjectID:      projectID,
				AllowedOrigins: origins,
			},
			Found: true,
		},
	}}
}

func newTestSiteAuthenticator(keys SiteKeyReader) *SiteAuthenticator {
	return NewSite(keys, Options{CacheTTL: time.Minute, CacheMaxEntries: 100})
}

func TestSiteAuthenticateResolvesTenancyAndOrigins(t *testing.T) {
	auth := newTestSiteAuthenticator(seededSiteKeys(validSiteKey, "https://app.example.com"))

	identity, err := auth.Authenticate(context.Background(), validSiteKey)
	if err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}
	if identity.OrgID != orgID {
		t.Errorf("OrgID = %q, want %q", identity.OrgID, orgID)
	}
	if identity.ProjectID != projectID {
		t.Errorf("ProjectID = %q, want %q", identity.ProjectID, projectID)
	}
	// The whole key, not a prefix: there is no secret half to leave behind.
	if identity.KeyPrefix != validSiteKey {
		t.Errorf("KeyPrefix = %q, want the whole key %q", identity.KeyPrefix, validSiteKey)
	}
	if len(identity.AllowedOrigins) != 1 || identity.AllowedOrigins[0] != "https://app.example.com" {
		t.Errorf("AllowedOrigins = %v, want the row's list", identity.AllowedOrigins)
	}
}

// The permission boundary, asserted rather than described. A public key must not
// open the server doors, and a secret key must not open the browser one.
func TestASiteKeyMayNotIngestServerTelemetry(t *testing.T) {
	auth := newTestSiteAuthenticator(seededSiteKeys(validSiteKey, "https://app.example.com"))

	identity, err := auth.Authenticate(context.Background(), validSiteKey)
	if err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}
	if identity.Kind != KindPublic {
		t.Errorf("Kind = %q, want %q", identity.Kind, KindPublic)
	}
	if identity.MayIngestServerTelemetry() {
		t.Error("a public key opened the server doors; it may only accept browser telemetry")
	}
	if !identity.MayIngestBrowserTelemetry() {
		t.Error("a public key was refused the browser door it exists for")
	}
}

func TestASecretKeyMayNotIngestBrowserTelemetry(t *testing.T) {
	auth := newTestAuthenticator(seededKeys(validKey))

	identity, err := auth.Authenticate(context.Background(), validKey)
	if err != nil {
		t.Fatalf("Authenticate() = %v, want no error", err)
	}
	if identity.Kind != KindSecret {
		t.Errorf("Kind = %q, want %q", identity.Kind, KindSecret)
	}
	if identity.MayIngestBrowserTelemetry() {
		t.Error("a secret key opened the browser door, which would teach customers to put one in a page")
	}
	if !identity.MayIngestServerTelemetry() {
		t.Error("a secret key was refused the server doors it exists for")
	}
}

// An Identity nobody set a kind on must open nothing. Both predicates are
// allow-lists so a future third kind fails closed rather than inheriting rights.
func TestAnIdentityWithNoKindOpensNothing(t *testing.T) {
	var unset Identity
	if unset.MayIngestServerTelemetry() || unset.MayIngestBrowserTelemetry() {
		t.Error("a kindless identity granted ingest; the predicates are not allow-lists")
	}
}

// The two doors cannot be crossed even before a lookup happens: the schemes
// differ, so a key of the wrong kind is malformed rather than merely unknown.
func TestEachDoorRefusesTheOtherKindsFormat(t *testing.T) {
	siteAuth := newTestSiteAuthenticator(seededSiteKeys(validSiteKey))
	keys := seededKeys(validKey)
	secretAuth := newTestAuthenticator(keys)

	if _, err := siteAuth.Authenticate(context.Background(), validKey); !errors.Is(err, ErrMalformedKey) {
		t.Errorf("browser door on a secret key = %v, want ErrMalformedKey", err)
	}
	if _, err := secretAuth.Authenticate(context.Background(), validSiteKey); !errors.Is(err, ErrMalformedKey) {
		t.Errorf("server door on a site key = %v, want ErrMalformedKey", err)
	}
	if keys.reads != 0 {
		t.Errorf("api_keys was read %d times for a site key; format must refuse before lookup", keys.reads)
	}
}

// A site key the browser door has never heard of is refused, and the refusal is
// indistinguishable from a revoked one — the lookup excludes revoked rows.
func TestAnUnknownSiteKeyIsRefused(t *testing.T) {
	auth := newTestSiteAuthenticator(&fakeSiteKeys{rows: map[string]SiteCredential{}})

	if _, err := auth.Authenticate(context.Background(), validSiteKey); !errors.Is(err, ErrUnknownKey) {
		t.Errorf("Authenticate() = %v, want ErrUnknownKey", err)
	}
}

func TestAMissingSiteKeyIsItsOwnError(t *testing.T) {
	auth := newTestSiteAuthenticator(seededSiteKeys(validSiteKey))

	if _, err := auth.Authenticate(context.Background(), ""); !errors.Is(err, ErrMissingKey) {
		t.Errorf("Authenticate(\"\") = %v, want ErrMissingKey", err)
	}
}

// A control-plane failure is ours, not the caller's, so it must stay retryable.
func TestASiteLookupFailureIsNotTheCallersFault(t *testing.T) {
	auth := newTestSiteAuthenticator(&fakeSiteKeys{err: errors.New("connection refused")})

	_, err := auth.Authenticate(context.Background(), validSiteKey)
	if err == nil {
		t.Fatal("Authenticate() succeeded against an unreachable control plane")
	}
	if IsCallerFault(err) {
		t.Error("a database outage was reported as a bad key; the client would stop retrying")
	}
}

func TestTheSiteKeyCacheSparesRepeatedLookups(t *testing.T) {
	keys := seededSiteKeys(validSiteKey, "https://app.example.com")
	auth := newTestSiteAuthenticator(keys)

	for range 5 {
		if _, err := auth.Authenticate(context.Background(), validSiteKey); err != nil {
			t.Fatalf("Authenticate() = %v, want no error", err)
		}
	}
	if keys.reads != 1 {
		t.Errorf("site_keys was read %d times, want 1: every pageview would hit Postgres", keys.reads)
	}
}

// The throttle ADR 0081 established for the secret door holds here too: one write
// per key per cache period, never one per request.
func TestSiteKeyUseIsRecordedOncePerCachePeriod(t *testing.T) {
	uses := &recordedUses{}
	auth := NewSite(seededSiteKeys(validSiteKey), Options{
		CacheTTL:        time.Minute,
		CacheMaxEntries: 100,
		UseRecorder:     uses,
	})

	for range 5 {
		if _, err := auth.Authenticate(context.Background(), validSiteKey); err != nil {
			t.Fatalf("Authenticate() = %v, want no error", err)
		}
	}
	recorded := uses.all()
	if len(recorded) != 1 {
		t.Errorf("recorded %d uses across 5 authentications, want 1", len(recorded))
	}
	if len(recorded) == 1 && recorded[0].prefix != validSiteKey {
		t.Errorf("recorded use of %q, want the whole key %q", recorded[0].prefix, validSiteKey)
	}
}

// An unknown key must not stamp anything: the row it would name does not exist,
// and a recorder call for it is a write the drainer would attempt for nothing.
func TestAnUnknownSiteKeyRecordsNoUse(t *testing.T) {
	uses := &recordedUses{}
	auth := NewSite(&fakeSiteKeys{rows: map[string]SiteCredential{}}, Options{
		CacheTTL:        time.Minute,
		CacheMaxEntries: 100,
		UseRecorder:     uses,
	})

	_, _ = auth.Authenticate(context.Background(), validSiteKey)

	if recorded := uses.all(); len(recorded) != 0 {
		t.Errorf("recorded %d uses for a key that does not exist, want 0", len(recorded))
	}
}

/* ------------------------------------- where a credential may be read from */

func requestWith(header, query string) *http.Request {
	target := "/v1/traces"
	if query != "" {
		target += "?" + SiteKeyQueryParam + "=" + query
	}
	r := httptest.NewRequest(http.MethodPost, target, nil)
	if header != "" {
		r.Header.Set(HeaderName, header)
	}
	return r
}

// browserDoor and serverDoor are the two Gatekeepers, read as a door reads them:
// the type that resolves a kind is the only type that can read one off a request.
func browserDoor() Gatekeeper { return newTestSiteAuthenticator(seededSiteKeys(validSiteKey)) }

func serverDoor() Gatekeeper { return newTestAuthenticator(seededKeys(validKey)) }

// sendBeacon cannot set headers, so the browser door has to accept a query
// parameter or lose every unload flush.
func TestTheBrowserDoorReadsASiteKeyFromTheQueryString(t *testing.T) {
	if got := browserDoor().PresentedKey(requestWith("", validSiteKey)); got != validSiteKey {
		t.Errorf("PresentedKey() = %q, want %q", got, validSiteKey)
	}
}

func TestAHeaderWinsOverTheQueryStringOnTheBrowserDoor(t *testing.T) {
	other := "nvp_live_otherotherotherother"
	if got := browserDoor().PresentedKey(requestWith(validSiteKey, other)); got != validSiteKey {
		t.Errorf("PresentedKey() = %q, want the header's %q", got, validSiteKey)
	}
}

// The asymmetry that makes the query string safe: it may carry the kind that was
// never secret, and only that kind.
func TestASecretKeyInTheQueryStringIsNotEvenRead(t *testing.T) {
	if got := browserDoor().PresentedKey(requestWith("", validKey)); got != "" {
		t.Errorf("PresentedKey() = %q, want \"\": a secret key must not be read from a URL", got)
	}
}

func TestTheServerDoorNeverReadsAKeyFromTheQueryString(t *testing.T) {
	// Presented the only way a secret key could reach a URL, under either name.
	r := httptest.NewRequest(http.MethodPost, "/v1/traces?"+SiteKeyQueryParam+"="+validKey, nil)
	if got := serverDoor().PresentedKey(r); got != "" {
		t.Errorf("PresentedKey() = %q, want \"\": a secret key belongs in a header only", got)
	}

	withHeader := httptest.NewRequest(http.MethodPost, "/v1/traces", nil)
	withHeader.Header.Set(HeaderName, validKey)
	if got := serverDoor().PresentedKey(withHeader); got != validKey {
		t.Errorf("PresentedKey() = %q, want the header's %q", got, validKey)
	}
}

// End to end through the authenticator, not just the reader.
func TestASecretKeyPresentedToTheBrowserDoorAuthenticatesNothing(t *testing.T) {
	auth := newTestSiteAuthenticator(seededSiteKeys(validSiteKey))
	presented := auth.PresentedKey(requestWith("", validKey))

	if _, err := auth.Authenticate(context.Background(), presented); !errors.Is(err, ErrMissingKey) {
		t.Errorf("Authenticate() = %v, want ErrMissingKey: nothing should have been read", err)
	}
}
