package derive

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/insertdedup"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/storefailure"
)

// overCapTable is the insert target, named once so the statement and the column
// list cannot drift apart.
const overCapTable = "services_over_cap_1m"

// columnServicesLeftOut is the one column this table adds to the shared ones.
const columnServicesLeftOut = "services_left_out"

// overCapInsert names the target columns rather than relying on their order in
// the table (migration 000039).
const overCapInsert = "INSERT INTO " + overCapTable + " (" + columnTenantID + ", " + columnRetentionDays +
	", " + columnMinute + ", " + columnServicesLeftOut + ") VALUES"

// overCapBlock is one insert's worth of over-cap rows, held column by column.
// It is reused across flushes: resetting keeps the capacity.
type overCapBlock struct {
	tenantID      proto.ColStr
	retentionDays proto.ColUInt16
	minute        proto.ColDateTime
	servicesLeft  proto.ColUInt64

	columns proto.Input
}

func newOverCapBlock() *overCapBlock {
	b := &overCapBlock{}
	b.columns = proto.Input{
		{Name: columnTenantID, Data: &b.tenantID},
		{Name: columnRetentionDays, Data: &b.retentionDays},
		{Name: columnMinute, Data: &b.minute},
		{Name: columnServicesLeftOut, Data: &b.servicesLeft},
	}
	return b
}

func (b *overCapBlock) append(entry ServiceCap) {
	b.tenantID.Append(entry.TenantID)
	b.retentionDays.Append(entry.RetentionDays)
	b.minute.Append(entry.Minute)
	b.servicesLeft.Append(entry.Services)
}

func (b *overCapBlock) rows() int { return b.tenantID.Rows() }

func (b *overCapBlock) reset() { b.columns.Reset() }

// overCapDeduplicationToken makes a retried insert recognisable as one. The
// table sums, so a retry after a lost acknowledgement would otherwise double a
// minute's count; the token is the rows' own, as `service_stats_1m`'s is.
func overCapDeduplicationToken(entries []ServiceCap) string {
	digest := insertdedup.NewDigest()
	for _, entry := range entries {
		digest.AddRow(entry.Minute.UTC(), entry.TenantID, entry.RetentionDays, entry.Services)
	}
	return digest.Token()
}

// OverCapWriter inserts the record of tenant-minutes the service cap left
// services out of.
type OverCapWriter struct {
	pool  *chpool.Pool
	block *overCapBlock
}

// NewOverCapWriter returns a writer over an existing pool. The caller owns the
// pool.
func NewOverCapWriter(pool *chpool.Pool) *OverCapWriter {
	return &OverCapWriter{pool: pool, block: newOverCapBlock()}
}

// Insert writes the entries as one columnar batch. It returns an error rather
// than retrying: the caller holds the Kafka offsets and commits only once this
// has succeeded, so a failure is retried by re-consuming.
func (w *OverCapWriter) Insert(ctx context.Context, entries []ServiceCap) error {
	if len(entries) == 0 {
		return nil
	}
	w.block.reset()
	for _, entry := range entries {
		w.block.append(entry)
	}

	query := ch.Query{
		Body:     overCapInsert,
		Input:    w.block.columns,
		Settings: []ch.Setting{{Key: insertdedup.Setting, Value: overCapDeduplicationToken(entries)}},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return storefailure.Judged(fmt.Errorf("insert %d over-cap rows: %w", w.block.rows(), err))
	}
	return nil
}
