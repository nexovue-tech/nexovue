package usage

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Store is the control plane as this job needs it: the durable record of what
// each partition has already been counted through, and the one transaction that
// advances it while adding the counters it made exact.
//
// The two halves are one transaction on purpose. Delivery is at-least-once, so
// a replay is not an error case but the normal consequence of a crash between
// a write and a commit; and an addition applied twice is a customer billed
// twice. ADR 0060 and 0061 had to solve that for the ClickHouse counters with a
// BEFORE/AFTER bracket around the insert, because ClickHouse has no transaction
// to put both writes in, and 0075 then leaned on the store's own block
// deduplication for the window that bracket leaves open. Postgres does have the
// transaction, so the same problem here has no such window: the offsets and the
// counters move together or neither moves.
type Store interface {
	// CountedThrough reads the highest offset already applied, per partition.
	CountedThrough(ctx context.Context) (map[int32]int64, error)

	// Apply adds rows to `usage_daily` and advances the offsets, atomically.
	// Applying the same flush twice must leave the counters where the first
	// one left them.
	Apply(ctx context.Context, rows []Row, offsets map[int32]int64) (Applied, error)
}

// Applied is what one flush did, for the line the pipeline logs.
type Applied struct {
	// AlreadyCounted means the offsets this flush claims were already at or
	// past their target, so its counters had landed and were not added again.
	// A retry after a lost acknowledgement is the case this exists for.
	AlreadyCounted bool

	// Written is how many tenant-days reached the table, and Unattributed how
	// many were dropped because no live project matched them.
	Written      int
	Unattributed int
}

// offsetsTable is this job's own record of what it has counted, keyed by topic
// and partition. It is written in the same transaction as the counters, which
// is what makes a replay harmless.
//
// NOT YET CREATED BY A MIGRATION. The control plane owns its schema in
// `apps/api/migrations`, and this table has to be added there before a worker
// can meter anything:
//
//	topic text, partition_id integer, counted_through bigint,
//	updated_at timestamptz not null default now(),
//	primary key (topic, partition_id)
const offsetsTable = "usage_ingest_offsets"

// advanceOffsets records how far each partition has been counted and reports,
// through the rows it affects, whether this flush is new work.
//
// The WHERE on the conflict is the whole guard: an offset that does not advance
// updates nothing, so a flush retried after its transaction already committed
// affects zero rows and the caller knows not to add its counters again. It also
// takes the row lock, so two workers briefly sharing a partition through a
// rebalance serialize rather than both counting.
const advanceOffsets = `
	INSERT INTO ` + offsetsTable + ` AS settled (topic, partition_id, counted_through, updated_at)
	SELECT $1, position.partition_id, position.counted_through, now()
	FROM unnest($2::int[], $3::bigint[]) AS position(partition_id, counted_through)
	ON CONFLICT (topic, partition_id) DO UPDATE
	SET counted_through = excluded.counted_through, updated_at = now()
	WHERE settled.counted_through < excluded.counted_through`

// addCounters folds one flush into `usage_daily`.
//
// The update adds rather than replaces: a day's row is written many times as
// the day goes on, and a restart at noon must not erase the morning.
//
// Ids arrive as text and are joined to `projects` rather than cast, which does
// three jobs at once. The join supplies the typed values the foreign keys need;
// a malformed id matches nothing instead of failing the whole flush and
// wedging the pipeline on one poisoned message; and matching org and project
// together means a message claiming another organization's project writes
// nothing, which is the tenant-isolation invariant enforced at the write.
const addCounters = `
	INSERT INTO usage_daily AS existing
		(org_id, project_id, day, spans, logs, metric_points, bytes)
	SELECT projects.org_id, projects.id, counted.day,
	       counted.spans, counted.logs, counted.metric_points, counted.bytes
	FROM unnest($1::text[], $2::text[], $3::date[],
	            $4::bigint[], $5::bigint[], $6::bigint[], $7::bigint[])
		AS counted(org_id, project_id, day, spans, logs, metric_points, bytes)
	JOIN projects
		ON projects.id::text = counted.project_id
		AND projects.org_id::text = counted.org_id
	ON CONFLICT (org_id, project_id, day) DO UPDATE
	SET spans         = existing.spans + excluded.spans,
	    logs          = existing.logs + excluded.logs,
	    metric_points = existing.metric_points + excluded.metric_points,
	    bytes         = existing.bytes + excluded.bytes`

const readOffsets = `
	SELECT partition_id, counted_through FROM ` + offsetsTable + ` WHERE topic = $1`

// PostgresStore writes one topic's usage into the control plane.
type PostgresStore struct {
	pool  *pgxpool.Pool
	topic string
}

// NewPostgresStore returns a store over an existing pool. The caller owns it.
func NewPostgresStore(pool *pgxpool.Pool, topic string) *PostgresStore {
	return &PostgresStore{pool: pool, topic: topic}
}

// CountedThrough implements Store.
func (s *PostgresStore) CountedThrough(ctx context.Context) (map[int32]int64, error) {
	rows, err := s.pool.Query(ctx, readOffsets, s.topic)
	if err != nil {
		return nil, fmt.Errorf("read counted offsets for %s: %w", s.topic, err)
	}
	defer rows.Close()

	counted := make(map[int32]int64)
	for rows.Next() {
		var (
			partition int32
			offset    int64
		)
		if err := rows.Scan(&partition, &offset); err != nil {
			return nil, fmt.Errorf("read a counted offset for %s: %w", s.topic, err)
		}
		counted[partition] = offset
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("read counted offsets for %s: %w", s.topic, err)
	}
	return counted, nil
}

// Apply implements Store.
func (s *PostgresStore) Apply(
	ctx context.Context,
	rows []Row,
	offsets map[int32]int64,
) (result Applied, err error) {
	if len(offsets) == 0 {
		// Counters can only come from records, and records always move an
		// offset, so nothing to advance means nothing to add.
		return Applied{}, nil
	}

	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return Applied{}, fmt.Errorf("begin usage transaction: %w", err)
	}
	defer func() {
		// Rollback after a commit is a no-op, so this only ever undoes a
		// transaction that failed part way.
		if rollbackErr := tx.Rollback(ctx); rollbackErr != nil &&
			!isTransactionClosed(rollbackErr) && err == nil {
			err = fmt.Errorf("roll back usage transaction: %w", rollbackErr)
		}
	}()

	advanced, err := s.advance(ctx, tx, offsets)
	if err != nil {
		return Applied{}, err
	}
	if advanced == 0 {
		// Every offset was already at or past its target: this flush landed
		// before, and its counters are already in the table.
		return Applied{AlreadyCounted: true}, nil
	}

	written, err := s.add(ctx, tx, rows)
	if err != nil {
		return Applied{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return Applied{}, fmt.Errorf("commit usage transaction: %w", err)
	}
	return Applied{Written: written, Unattributed: len(rows) - written}, nil
}

// advance records the offsets and reports how many actually moved.
func (s *PostgresStore) advance(
	ctx context.Context,
	tx pgx.Tx,
	offsets map[int32]int64,
) (int, error) {
	partitions := make([]int32, 0, len(offsets))
	through := make([]int64, 0, len(offsets))
	for partition, offset := range offsets {
		partitions = append(partitions, partition)
		through = append(through, offset)
	}

	tag, err := tx.Exec(ctx, advanceOffsets, s.topic, partitions, through)
	if err != nil {
		return 0, fmt.Errorf("advance counted offsets for %s: %w", s.topic, err)
	}
	return int(tag.RowsAffected()), nil
}

// add folds the rows into usage_daily and reports how many reached it.
func (s *PostgresStore) add(ctx context.Context, tx pgx.Tx, rows []Row) (int, error) {
	if len(rows) == 0 {
		return 0, nil
	}

	columns := columnsOf(rows)
	tag, err := tx.Exec(ctx, addCounters,
		columns.orgIDs, columns.projectIDs, columns.days,
		columns.spans, columns.logs, columns.metricPoints, columns.bytes)
	if err != nil {
		return 0, fmt.Errorf("add usage counters: %w", err)
	}
	return int(tag.RowsAffected()), nil
}

// columns is one flush transposed, because the insert takes arrays rather than
// a statement per row.
type columns struct {
	orgIDs       []string
	projectIDs   []string
	days         []time.Time
	spans        []int64
	logs         []int64
	metricPoints []int64
	bytes        []int64
}

func columnsOf(rows []Row) columns {
	held := columns{
		orgIDs:       make([]string, 0, len(rows)),
		projectIDs:   make([]string, 0, len(rows)),
		days:         make([]time.Time, 0, len(rows)),
		spans:        make([]int64, 0, len(rows)),
		logs:         make([]int64, 0, len(rows)),
		metricPoints: make([]int64, 0, len(rows)),
		bytes:        make([]int64, 0, len(rows)),
	}
	for _, row := range rows {
		held.orgIDs = append(held.orgIDs, row.OrgID)
		held.projectIDs = append(held.projectIDs, row.ProjectID)
		held.days = append(held.days, row.Day)
		held.spans = append(held.spans, row.Spans)
		held.logs = append(held.logs, row.Logs)
		held.metricPoints = append(held.metricPoints, row.MetricPoints)
		held.bytes = append(held.bytes, row.Bytes)
	}
	return held
}

// isTransactionClosed reports the benign rollback: the transaction is already
// finished, which is what every committed one returns.
func isTransactionClosed(err error) bool {
	return errors.Is(err, pgx.ErrTxClosed)
}
