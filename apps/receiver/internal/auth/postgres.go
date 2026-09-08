package auth

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// One statement resolves both ids: api_keys carries org_id as well as
// project_id precisely so this is not a three-table join on the ingest path.
// A revoked key is not a row this query can return.
//
// **The join to projects is not free and is not optional.** It costs the ingest
// path a lookup this comment once existed to avoid, and it buys the thing that
// was missing until 2026-09-08: a key whose project is soft-deleted still
// authenticated here, while `projectconfig/postgres.go` filters
// `projects.deleted_at IS NULL` and drops that project from the settings
// refresh. So a deleted project kept accepting telemetry from the internet
// **under default masking rather than its own** — a customer who had configured
// `mask_keys` lost it at the moment they pressed delete. The row was also still
// spending the organization's allowance while being unreachable on every screen.
// One cached row per key prefix carries this, so it is one join per cache miss.
const lookupByPrefix = `
	SELECT k.org_id::text, k.project_id::text, k.key_hash
	FROM api_keys k
	JOIN projects p ON p.id = k.project_id
	WHERE k.key_prefix = $1 AND k.revoked_at IS NULL AND p.deleted_at IS NULL`

// The one write the ingest path makes to the control plane. Guarded on the
// timestamp so that receivers writing out of order — several processes, each with
// its own cache — can only move `last_used_at` forward. Revoked keys are excluded
// for the same reason the lookup excludes them: a withdrawn key is not in use.
const markUsed = `
	UPDATE api_keys
	SET last_used_at = $2
	WHERE key_prefix = $1
	  AND revoked_at IS NULL
	  AND (last_used_at IS NULL OR last_used_at < $2)`

// The browser door's lookup. It names site_keys, and only site_keys, which is
// what makes "a public key answered where a secret one was expected" unreachable
// rather than merely unintended (ADR 0086).
//
// There is no key_hash to select because the table has no such column: a site key
// is stored whole and in the clear, so this exact match on the unique index is
// the entire verification. allowed_origins comes back with it because the
// transport needs it in the same round trip the tenancy arrives in.
// The same `projects.deleted_at` join as the server door, for the same reason —
// rule 5: one defect in two sibling lookups is one fix, not one lookup's.
const lookupSiteKey = `
	SELECT k.org_id::text, k.project_id::text, k.allowed_origins
	FROM site_keys k
	JOIN projects p ON p.id = k.project_id
	WHERE k.public_key = $1 AND k.revoked_at IS NULL AND p.deleted_at IS NULL`

// The browser door's equivalent of markUsed, guarded the same way so several
// receivers can only move the timestamp forward.
const markSiteKeyUsed = `
	UPDATE site_keys
	SET last_used_at = $2
	WHERE public_key = $1
	  AND revoked_at IS NULL
	  AND (last_used_at IS NULL OR last_used_at < $2)`

// PostgresKeys reads api_keys, and records when one authenticated. It is the
// only place the receiver touches the control-plane database.
type PostgresKeys struct {
	pool *pgxpool.Pool
}

// NewPostgresKeys returns a reader over an existing pool.
func NewPostgresKeys(pool *pgxpool.Pool) *PostgresKeys {
	return &PostgresKeys{pool: pool}
}

// CredentialFor reads the live api_keys row a lookup prefix names.
func (k *PostgresKeys) CredentialFor(ctx context.Context, prefix string) (Credential, error) {
	var found Credential
	err := k.pool.QueryRow(ctx, lookupByPrefix, prefix).
		Scan(&found.Identity.OrgID, &found.Identity.ProjectID, &found.KeyHash)

	switch {
	case errors.Is(err, pgx.ErrNoRows):
		// A miss is an answer, not a failure: it caches like any other.
		return Credential{Found: false}, nil
	case err != nil:
		return Credential{}, fmt.Errorf("querying api_keys: %w", err)
	}
	found.Found = true
	return found, nil
}

// MarkUsed records that the key with this prefix authenticated at `at`.
func (k *PostgresKeys) MarkUsed(ctx context.Context, prefix string, at time.Time) error {
	if _, err := k.pool.Exec(ctx, markUsed, prefix, at); err != nil {
		return fmt.Errorf("recording api_keys.last_used_at: %w", err)
	}
	return nil
}

// PostgresSiteKeys reads site_keys, and records when one authenticated.
//
// A separate type over the same pool rather than two more methods on
// PostgresKeys: the type a door is handed then cannot reach the other table at
// all, which is the same argument one level up from the two tables themselves.
type PostgresSiteKeys struct {
	pool *pgxpool.Pool
}

// NewPostgresSiteKeys returns a site-key reader over an existing pool.
func NewPostgresSiteKeys(pool *pgxpool.Pool) *PostgresSiteKeys {
	return &PostgresSiteKeys{pool: pool}
}

// SiteCredentialFor reads the live site_keys row this public key names.
func (k *PostgresSiteKeys) SiteCredentialFor(
	ctx context.Context, publicKey string,
) (SiteCredential, error) {
	var found SiteCredential
	err := k.pool.QueryRow(ctx, lookupSiteKey, publicKey).Scan(
		&found.Identity.OrgID,
		&found.Identity.ProjectID,
		&found.Identity.AllowedOrigins,
	)

	switch {
	case errors.Is(err, pgx.ErrNoRows):
		// A miss is an answer, not a failure: it caches like any other.
		return SiteCredential{Found: false}, nil
	case err != nil:
		return SiteCredential{}, fmt.Errorf("querying site_keys: %w", err)
	}
	found.Found = true
	return found, nil
}

// MarkUsed records that this public key authenticated at `at`.
func (k *PostgresSiteKeys) MarkUsed(ctx context.Context, publicKey string, at time.Time) error {
	if _, err := k.pool.Exec(ctx, markSiteKeyUsed, publicKey, at); err != nil {
		return fmt.Errorf("recording site_keys.last_used_at: %w", err)
	}
	return nil
}
