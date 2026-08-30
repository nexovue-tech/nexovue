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
// The join to `project_first_traces` is a LEFT JOIN because a project that has
// stored nothing under its carve-out has no row there — which is the state
// every project is created in, and it reads as a count of zero.
const selectProjectConfig = `
	SELECT projects.id::text,
	       coalesce(projects.masking_config, '{}'::jsonb)::text,
	       coalesce(projects.sampling_config, '{}'::jsonb)::text,
	       project_retention.raw_retention_days,
	       project_retention.derived_retention_days,
	       coalesce(project_first_traces.traces_stored, 0)
	FROM projects
	JOIN project_retention ON project_retention.project_id = projects.id
	LEFT JOIN project_first_traces ON project_first_traces.project_id = projects.id
	WHERE projects.deleted_at IS NULL`

// One statement adds every worker's progress through the first-traces carve-out.
//
// It adds rather than sets, so two workers counting the same project's first
// traces sum instead of overwriting each other. It joins `projects` rather than
// inserting the ids straight in, so an id whose project has been deleted between
// the sampler counting a trace and this flush is skipped: on the foreign key it
// would fail the whole statement instead, and the same batch would be retried
// for ever.
const addFirstTracesStored = `
	INSERT INTO project_first_traces (project_id, traces_stored)
	SELECT projects.id, stored.traces
	FROM unnest($1::text[], $2::bigint[]) AS stored(project_id, traces)
	JOIN projects ON projects.id::text = stored.project_id
	WHERE projects.deleted_at IS NULL
	ON CONFLICT (project_id) DO UPDATE
	SET traces_stored = project_first_traces.traces_stored + excluded.traces_stored,
	    updated_at = now()`

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

// PostgresReader is the control plane as the worker sees it: it reads every
// project's configuration and records how far each one has got through its
// first-traces carve-out. It is the only place the worker touches Postgres.
type PostgresReader struct {
	pool *pgxpool.Pool
}

// NewPostgresReader returns a store over an existing pool.
func NewPostgresReader(pool *pgxpool.Pool) *PostgresReader {
	return &PostgresReader{pool: pool}
}

// ReadProjects implements Store.
func (r *PostgresReader) ReadProjects(ctx context.Context) ([]Row, error) {
	rows, err := r.pool.Query(ctx, selectProjectConfig)
	if err != nil {
		return nil, fmt.Errorf("querying projects: %w", err)
	}
	defer rows.Close()

	var loaded []Row
	for rows.Next() {
		var (
			projectID   string
			masking     string
			sampling    string
			raw         int32
			derived     int32
			firstTraces int64
		)
		if err := rows.Scan(&projectID, &masking, &sampling, &raw, &derived, &firstTraces); err != nil {
			return nil, fmt.Errorf("reading a projects row: %w", err)
		}
		loaded = append(loaded, Row{
			ProjectID:            projectID,
			MaskingConfig:        []byte(masking),
			SamplingConfig:       []byte(sampling),
			RawRetentionDays:     retentionDays(raw),
			DerivedRetentionDays: retentionDays(derived),
			FirstTracesStored:    firstTraces,
		})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading projects: %w", err)
	}
	return loaded, nil
}

// AddFirstTracesStored implements Store.
func (r *PostgresReader) AddFirstTracesStored(ctx context.Context, stored map[string]int64) error {
	if len(stored) == 0 {
		return nil
	}

	projectIDs := make([]string, 0, len(stored))
	traces := make([]int64, 0, len(stored))
	for projectID, count := range stored {
		projectIDs = append(projectIDs, projectID)
		traces = append(traces, count)
	}
	if _, err := r.pool.Exec(ctx, addFirstTracesStored, projectIDs, traces); err != nil {
		return fmt.Errorf("recording first traces stored: %w", err)
	}
	return nil
}
