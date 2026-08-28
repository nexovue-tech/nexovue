package projectconfig

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// One statement loads every live project's configuration and the retention it
// actually gets. The retention comes from `project_retention`, which is where
// the plan's grant and the project's own request are reconciled — resolving it
// here as well would be a second definition free to disagree with the first.
// It is a table scan, deliberately: projects is small, this runs once per
// refresh interval rather than per span, and one round trip beats a query per
// project id the pipeline happens to see.
const selectProjectConfig = `
	SELECT projects.id::text,
	       coalesce(projects.masking_config, '{}'::jsonb)::text,
	       coalesce(projects.sampling_config, '{}'::jsonb)::text,
	       project_retention.raw_retention_days,
	       project_retention.derived_retention_days
	FROM projects
	JOIN project_retention ON project_retention.project_id = projects.id
	WHERE projects.deleted_at IS NULL`

// maxRetentionDays is the widest window the row column can carry. The control
// plane's own constraint keeps values far below it; this is what makes the
// narrowing safe rather than merely likely, and a value outside it reads as
// "unset" so the deployment default applies instead of a wrapped number.
const maxRetentionDays = 36500

// retentionDays narrows the control plane's value to the width the row column
// uses. Zero for anything out of range, which the refresher treats as unset.
func retentionDays(days int32) uint16 {
	if days <= 0 || days > maxRetentionDays {
		return 0
	}
	return uint16(days)
}

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
			raw       int32
			derived   int32
		)
		if err := rows.Scan(&projectID, &masking, &sampling, &raw, &derived); err != nil {
			return nil, fmt.Errorf("reading a projects row: %w", err)
		}
		loaded = append(loaded, Row{
			ProjectID:            projectID,
			MaskingConfig:        []byte(masking),
			SamplingConfig:       []byte(sampling),
			RawRetentionDays:     retentionDays(raw),
			DerivedRetentionDays: retentionDays(derived),
		})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading projects: %w", err)
	}
	return loaded, nil
}
