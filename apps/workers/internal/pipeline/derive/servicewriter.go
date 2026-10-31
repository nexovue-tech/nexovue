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

// servicesTable is the insert target, named once so the statement and the
// column list cannot drift apart.
const servicesTable = "service_stats_1m"

// serviceInput is the shape streamed over the native protocol. The buckets
// travel as a plain array; the table stores a `sumForEach` state, which only the
// server builds (migration 000035).
const serviceInput = "tenant_id String, derived_retention_days UInt16, project_id String, " +
	"service_name String, minute DateTime, request_count UInt64, error_count UInt64, " +
	"latency_buckets Array(UInt32)"

// serviceColumns names the target columns rather than relying on their order in
// the table.
const serviceColumns = "(tenant_id, derived_retention_days, project_id, service_name, minute, " +
	"request_count, error_count, latency_buckets)"

// serviceInsert folds each row's buckets into the state the table stores, so
// rows from every worker and every late reopening add bucket by bucket. The
// trailing FORMAT is required by an INSERT that reads from `input()`.
const serviceInsert = "INSERT INTO " + servicesTable + " " + serviceColumns +
	" SELECT tenant_id, derived_retention_days, project_id, service_name, minute, " +
	"request_count, error_count, arrayReduce('sumForEachState', [latency_buckets]) " +
	"FROM input('" + serviceInput + "') FORMAT Native"

// serviceBlock is one insert's worth of service rows, held column by column. It
// is reused across flushes: resetting keeps the capacity.
type serviceBlock struct {
	tenantID       proto.ColStr
	retentionDays  proto.ColUInt16
	projectID      proto.ColStr
	serviceName    proto.ColStr
	minute         proto.ColDateTime
	requestCount   proto.ColUInt64
	errorCount     proto.ColUInt64
	latencyBuckets *proto.ColArr[uint32]

	columns proto.Input
}

func newServiceBlock() *serviceBlock {
	b := &serviceBlock{latencyBuckets: proto.NewArray[uint32](new(proto.ColUInt32))}
	b.columns = proto.Input{
		{Name: columnTenantID, Data: &b.tenantID},
		{Name: "derived_retention_days", Data: &b.retentionDays},
		{Name: columnProjectID, Data: &b.projectID},
		{Name: columnServiceName, Data: &b.serviceName},
		{Name: "minute", Data: &b.minute},
		{Name: "request_count", Data: &b.requestCount},
		{Name: "error_count", Data: &b.errorCount},
		{Name: "latency_buckets", Data: b.latencyBuckets},
	}
	return b
}

func (b *serviceBlock) append(stat ServiceStat) {
	b.tenantID.Append(stat.TenantID)
	b.retentionDays.Append(stat.RetentionDays)
	b.projectID.Append(stat.ProjectID)
	b.serviceName.Append(stat.ServiceName)
	b.minute.Append(stat.Minute)
	b.requestCount.Append(stat.RequestCount)
	b.errorCount.Append(stat.ErrorCount)
	b.latencyBuckets.Append(stat.LatencyBuckets[:])
}

func (b *serviceBlock) rows() int { return b.tenantID.Rows() }

func (b *serviceBlock) reset() { b.columns.Reset() }

// serviceDeduplicationToken makes a retried insert recognisable as one, for the
// reason endpoint rows need it: the `input()` form is not covered by the store's
// own block hashing (insertdedup).
func serviceDeduplicationToken(stats []ServiceStat) string {
	digest := insertdedup.NewDigest()
	for _, stat := range stats {
		digest.AddRow(stat.Minute.UTC(), stat.TenantID, stat.ProjectID, stat.RetentionDays,
			stat.ServiceName, stat.RequestCount, stat.ErrorCount, stat.LatencyBuckets)
	}
	return digest.Token()
}

// ServiceWriter inserts the derived service rollup into ClickHouse.
type ServiceWriter struct {
	pool  *chpool.Pool
	block *serviceBlock
}

// NewServiceWriter returns a writer over an existing pool. The caller owns the
// pool.
func NewServiceWriter(pool *chpool.Pool) *ServiceWriter {
	return &ServiceWriter{pool: pool, block: newServiceBlock()}
}

// Insert writes the rows as one columnar batch. It returns an error rather than
// retrying: the caller holds the Kafka offsets and commits only once this has
// succeeded, so a failure is retried by re-consuming.
func (w *ServiceWriter) Insert(ctx context.Context, stats []ServiceStat) error {
	if len(stats) == 0 {
		return nil
	}
	w.block.reset()
	for _, stat := range stats {
		w.block.append(stat)
	}

	query := ch.Query{
		Body:     serviceInsert,
		Input:    w.block.columns,
		Settings: []ch.Setting{{Key: insertdedup.Setting, Value: serviceDeduplicationToken(stats)}},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return storefailure.Judged(fmt.Errorf("insert %d service rollup rows: %w", w.block.rows(), err))
	}
	return nil
}
