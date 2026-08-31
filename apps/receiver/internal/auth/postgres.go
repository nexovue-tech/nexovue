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
const lookupByPrefix = `
	SELECT org_id::text, project_id::text, key_hash
	FROM api_keys
	WHERE key_prefix = $1 AND revoked_at IS NULL`

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
