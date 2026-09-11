package auth

// The two credential shapes the doors cache. The map that holds them lives in
// internal/cache, shared with the plan allowance, so there is one answer to how
// stale our view of the control plane may be.

// Credential is one api_keys row as the hot path needs it. A row that does not
// exist is still a credential, with Found false, so misses cache like hits.
type Credential struct {
	Identity Identity

	// KeyHash is the lower-case SHA-256 hex digest of the whole key, as the
	// control plane stored it. Build one with HashKey.
	KeyHash string

	Found bool
}

// Exists implements cache.Value: a miss is held for less time than a hit.
func (c Credential) Exists() bool { return c.Found }

// SiteCredential is one site_keys row as the browser door needs it.
//
// It has no hash field, and that is the whole difference: a site key is stored
// in the clear because it ships inside the page that carries it, so the lookup
// matching *is* the verification. There is nothing left to compare, and no
// constant-time comparison to get wrong.
type SiteCredential struct {
	Identity Identity

	Found bool
}

// Exists implements cache.Value.
func (c SiteCredential) Exists() bool { return c.Found }
