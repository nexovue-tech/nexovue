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

// versionsTable is the insert target, named once so the statement and the
// column list cannot drift apart.
const versionsTable = "service_versions_1m"

// versionInput is the shape streamed over the native protocol: the service
// rollup's, with the version beside the service (migration 000038).
const versionInput = "tenant_id String, derived_retention_days UInt16, project_id String, " +
	"service_name String, minute DateTime, version String, request_count UInt64, " +
	"error_count UInt64, latency_buckets Array(UInt32)"

// versionColumns names the target columns rather than relying on their order in
// the table.
const versionColumns = "(tenant_id, derived_retention_days, project_id, service_name, minute, " +
	"version, request_count, error_count, latency_buckets)"

// versionInsert folds each row's buckets into the stored `sumForEach` state, as
// serviceInsert does.
const versionInsert = "INSERT INTO " + versionsTable + " " + versionColumns +
	" SELECT tenant_id, derived_retention_days, project_id, service_name, minute, version, " +
	"request_count, error_count, arrayReduce('sumForEachState', [latency_buckets]) " +
	"FROM input('" + versionInput + "') FORMAT Native"

// columnVersion is the one column this table adds to the service rollup's.
const columnVersion = "version"

// versionBlock is one insert's worth of version rows, held column by column and
// reused across flushes.
type versionBlock struct {
	tenantID       proto.ColStr
	retentionDays  proto.ColUInt16
	projectID      proto.ColStr
	serviceName    proto.ColStr
	minute         proto.ColDateTime
	version        proto.ColStr
	requestCount   proto.ColUInt64
	errorCount     proto.ColUInt64
	latencyBuckets *proto.ColArr[uint32]

	columns proto.Input
}

func newVersionBlock() *versionBlock {
	b := &versionBlock{latencyBuckets: proto.NewArray[uint32](new(proto.ColUInt32))}
	b.columns = proto.Input{
		{Name: columnTenantID, Data: &b.tenantID},
		{Name: columnRetentionDays, Data: &b.retentionDays},
		{Name: columnProjectID, Data: &b.projectID},
		{Name: columnServiceName, Data: &b.serviceName},
		{Name: columnMinute, Data: &b.minute},
		{Name: columnVersion, Data: &b.version},
		{Name: columnRequestCount, Data: &b.requestCount},
		{Name: columnErrorCount, Data: &b.errorCount},
		{Name: "latency_buckets", Data: b.latencyBuckets},
	}
	return b
}

func (b *versionBlock) append(stat VersionStat) {
	b.tenantID.Append(stat.TenantID)
	b.retentionDays.Append(stat.RetentionDays)
	b.projectID.Append(stat.ProjectID)
	b.serviceName.Append(stat.ServiceName)
	b.minute.Append(stat.Minute)
	b.version.Append(stat.Version)
	b.requestCount.Append(stat.RequestCount)
	b.errorCount.Append(stat.ErrorCount)
	b.latencyBuckets.Append(stat.LatencyBuckets[:])
}

func (b *versionBlock) rows() int { return b.tenantID.Rows() }

func (b *versionBlock) reset() { b.columns.Reset() }

// versionDeduplicationToken makes a retried insert recognisable as one, for the
// reason serviceDeduplicationToken does: the `input()` form is not covered by
// the store's own block hashing (insertdedup).
func versionDeduplicationToken(stats []VersionStat) string {
	digest := insertdedup.NewDigest()
	for _, stat := range stats {
		digest.AddRow(stat.Minute.UTC(), stat.TenantID, stat.ProjectID, stat.RetentionDays,
			stat.ServiceName, stat.Version, stat.RequestCount, stat.ErrorCount, stat.LatencyBuckets)
	}
	return digest.Token()
}

// VersionWriter inserts the derived version rollup into ClickHouse.
type VersionWriter struct {
	pool  *chpool.Pool
	block *versionBlock
}

// NewVersionWriter returns a writer over an existing pool. The caller owns the
// pool.
func NewVersionWriter(pool *chpool.Pool) *VersionWriter {
	return &VersionWriter{pool: pool, block: newVersionBlock()}
}

// Insert writes the rows as one columnar batch. It returns an error rather than
// retrying; its caller decides what a failure costs.
func (w *VersionWriter) Insert(ctx context.Context, stats []VersionStat) error {
	if len(stats) == 0 {
		return nil
	}
	w.block.reset()
	for _, stat := range stats {
		w.block.append(stat)
	}

	query := ch.Query{
		Body:     versionInsert,
		Input:    w.block.columns,
		Settings: []ch.Setting{{Key: insertdedup.Setting, Value: versionDeduplicationToken(stats)}},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return storefailure.Judged(fmt.Errorf("insert %d version rollup rows: %w", w.block.rows(), err))
	}
	return nil
}
