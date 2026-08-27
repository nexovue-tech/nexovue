package projectconfig

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// One statement loads both configuration documents for every live project.
// It is a table scan, deliberately: projects is small, this runs once per
// refresh interval rather than per span, and one round trip beats a query per
// project id the pipeline happens to see.
const selectProjectConfig = `
	SELECT id::text,
	       coalesce(masking_config, '{}'::jsonb)::text,
	       coalesce(sampling_config, '{}'::jsonb)::text
	FROM projects
	WHERE deleted_at IS NULL`

// PostgresReader reads project configuration from the control plane. It is the
// only place the worker touches Postgres.
type PostgresReader struct {
	pool *pgxpool.Pool
}

// NewPostgresReader returns a reader over an existing pool.
func NewPostgresReader(pool *pgxpool.Pool) *PostgresReader {
	return &PostgresReader{pool: pool}
}

// ReadProjects implements Reader.
func (r *PostgresReader) ReadProjects(ctx context.Context) ([]Row, error) {
	rows, err := r.pool.Query(ctx, selectProjectConfig)
	if err != nil {
		return nil, fmt.Errorf("querying projects: %w", err)
	}
	defer rows.Close()

	var loaded []Row
	for rows.Next() {
		var (
			projectID string
			masking   string
			sampling  string
		)
		if err := rows.Scan(&projectID, &masking, &sampling); err != nil {
			return nil, fmt.Errorf("reading a projects row: %w", err)
		}
		loaded = append(loaded, Row{
			ProjectID:      projectID,
			MaskingConfig:  []byte(masking),
			SamplingConfig: []byte(sampling),
		})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading projects: %w", err)
	}
	return loaded, nil
}
