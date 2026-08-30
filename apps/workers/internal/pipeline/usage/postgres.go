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

	// Apply adds rows to `usage_daily`, adds their services to
	// `usage_daily_services`, and advances the offsets, atomically. Applying
	// the same flush twice must leave the counters where the first one left
	// them.
	Apply(ctx context.Context, rows []Row, progress map[int32]Progress) (Applied, error)
}

// Progress is how far one partition has been counted, in both the units that
// matter: the offset, which is what makes a replay recognisable, and the
// receive time, which is what says whether a day has finished being counted.
//
// ReceivedThrough is zero when this flush counted nothing readable on the
// partition — a batch of replays, or of messages the meter could not decode.
// It is written as NULL then, and the stored value is kept.
type Progress struct {
	Offset          int64
	ReceivedThrough time.Time
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

	// Services is how many service rows were new. A service already recorded
	// for the day conflicts and is not counted here, so this falls to zero
	// once a customer's services have all been seen — which is what it should
	// do, the set being complete.
	Services int
}

// offsetsTable is this job's own record of what it has counted, keyed by topic
// and partition. It is written in the same transaction as the counters, which
// is what makes a replay harmless. Migrations 0009 and 0010 create it.
const offsetsTable = "usage_ingest_offsets"

// advanceOffsets records how far each partition has been counted and reports,
// through the rows it affects, whether this flush is new work.
//
// The WHERE on the conflict is the whole guard: an offset that does not advance
// updates nothing, so a flush retried after its transaction already committed
// affects zero rows and the caller knows not to add its counters again. It also
// takes the row lock, so two workers briefly sharing a partition through a
// rebalance serialize rather than both counting.
//
// GREATEST on the receive time, because a partition that has just moved to this
// worker starts with nothing remembered: the stored value is another worker's
// and must not be walked backwards by a flush that counted only replays.
// GREATEST ignores nulls, so "this flush read nothing new" keeps what is there.
const advanceOffsets = `
	INSERT INTO ` + offsetsTable + ` AS settled
		(topic, partition_id, counted_through, counted_through_received_at, updated_at)
	SELECT $1, incoming.partition_id, incoming.counted_through,
	       incoming.received_through, now()
	FROM unnest($2::int[], $3::bigint[], $4::timestamptz[])
		AS incoming(partition_id, counted_through, received_through)
	ON CONFLICT (topic, partition_id) DO UPDATE
	SET counted_through = excluded.counted_through,
	    counted_through_received_at = GREATEST(settled.counted_through_received_at,
	                                           excluded.counted_through_received_at),
	    updated_at = now()
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

// addServices records which services were billable on a day.
//
// A set, so the write is an insert that ignores what is already there. That is
// the whole replay story for the billing unit: unlike the counters above, which
// need the offset guard to avoid being added twice, inserting a name a second
// time cannot change the answer.
//
// Joined to `projects` for the same three reasons the counters are: typed ids,
// a malformed id that matches nothing rather than wedging the flush, and org
// and project matched together so no message can write into another tenant.
const addServices = `
	INSERT INTO usage_daily_services (org_id, project_id, day, service_name)
	SELECT projects.org_id, projects.id, seen.day, seen.service_name
	FROM unnest($1::text[], $2::text[], $3::date[], $4::text[])
		AS seen(org_id, project_id, day, service_name)
	JOIN projects
		ON projects.id::text = seen.project_id
		AND projects.org_id::text = seen.org_id
	ON CONFLICT (org_id, project_id, day, service_name) DO NOTHING`

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
	progress map[int32]Progress,
) (result Applied, err error) {
	if len(progress) == 0 {
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

	advanced, err := s.advance(ctx, tx, progress)
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
	services, err := s.addServices(ctx, tx, rows)
	if err != nil {
		return Applied{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return Applied{}, fmt.Errorf("commit usage transaction: %w", err)
	}
	return Applied{
		Written:      written,
		Unattributed: len(rows) - written,
		Services:     services,
	}, nil
}

// advance records how far each partition has been counted and reports how many
// actually moved.
func (s *PostgresStore) advance(
	ctx context.Context,
	tx pgx.Tx,
	progress map[int32]Progress,
) (int, error) {
	partitions := make([]int32, 0, len(progress))
	through := make([]int64, 0, len(progress))
	received := make([]*time.Time, 0, len(progress))
	for partition, reached := range progress {
		partitions = append(partitions, partition)
		through = append(through, reached.Offset)
		received = append(received, nullableTime(reached.ReceivedThrough))
	}

	tag, err := tx.Exec(ctx, advanceOffsets, s.topic, partitions, through, received)
	if err != nil {
		return 0, fmt.Errorf("advance counted offsets for %s: %w", s.topic, err)
	}
	return int(tag.RowsAffected()), nil
}

// nullableTime writes "this flush read nothing new on the partition" as NULL,
// so the stored watermark is kept rather than overwritten with year one.
func nullableTime(at time.Time) *time.Time {
	if at.IsZero() {
		return nil
	}
	return &at
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

// addServices adds each row's services to its day's set and reports how many
// were not already there.
func (s *PostgresStore) addServices(ctx context.Context, tx pgx.Tx, rows []Row) (int, error) {
	seen := servicesOf(rows)
	if len(seen.names) == 0 {
		return 0, nil
	}

	tag, err := tx.Exec(ctx, addServices,
		seen.orgIDs, seen.projectIDs, seen.days, seen.names)
	if err != nil {
		return 0, fmt.Errorf("add usage services: %w", err)
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

// services is one flush's service rows transposed: a tenant-day repeats once
// per service it carried, because the set is one row per service.
type services struct {
	orgIDs     []string
	projectIDs []string
	days       []time.Time
	names      []string
}

func servicesOf(rows []Row) services {
	var flattened services
	for _, row := range rows {
		for _, name := range row.Services {
			flattened.orgIDs = append(flattened.orgIDs, row.OrgID)
			flattened.projectIDs = append(flattened.projectIDs, row.ProjectID)
			flattened.days = append(flattened.days, row.Day)
			flattened.names = append(flattened.names, name)
		}
	}
	return flattened
}

// isTransactionClosed reports the benign rollback: the transaction is already
// finished, which is what every committed one returns.
func isTransactionClosed(err error) bool {
	return errors.Is(err, pgx.ErrTxClosed)
}
