package auth

import (
	"context"
	"errors"
	"fmt"

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

// PostgresKeys reads api_keys. It is the only place the receiver touches the
// control-plane database.
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
