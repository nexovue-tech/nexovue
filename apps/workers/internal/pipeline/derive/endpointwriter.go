package derive

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/insertdedup"
)

// endpointsTable is the insert target, named once so the statement and the
// column list cannot drift apart.
const endpointsTable = "endpoint_stats_1m"

// endpointInput is the shape streamed over the native protocol. It is not the
// table's shape: `latency_quantiles` is an AggregateFunction, and no client
// builds one of those — the state's serialised form belongs to the server. So
// the durations travel as an array and the *server* folds them into the state
// on the way in, which is the same `-State` construction the materialized view
// this replaced used to do.
const endpointInput = "tenant_id String, derived_retention_days UInt16, project_id String, " +
	"service_name String, span_kind String, span_name String, minute DateTime, " +
	"request_count UInt64, error_count UInt64, latency_ms Array(Float64)"

// endpointColumns names the target columns rather than relying on their order
// in the table, so a column added to `endpoint_stats_1m` cannot silently shift
// this insert onto the wrong ones.
const endpointColumns = "(tenant_id, derived_retention_days, project_id, service_name, " +
	"span_kind, span_name, minute, request_count, error_count, latency_quantiles)"

// endpointInsert folds each row's durations into the quantile state the table
// stores. arrayReduce applies the aggregate within one row, so the insert needs
// no GROUP BY: the derive stage has already grouped.
//
// The levels are the column's own — `quantilesMerge` refuses a state whose
// levels differ from the stored ones, so a read would fail loudly rather than
// return a wrong latency.
//
// The trailing FORMAT names how the rows that follow the statement are encoded.
// A plain `INSERT ... VALUES` implies it; an INSERT that reads from `input()`
// does not, and ClickHouse rejects the statement without it.
const endpointInsert = "INSERT INTO " + endpointsTable + " " + endpointColumns +
	" SELECT tenant_id, derived_retention_days, project_id, service_name, span_kind, " +
	"span_name, minute, request_count, error_count, " +
	"arrayReduce('quantilesState(0.5, 0.95, 0.99)', latency_ms) " +
	"FROM input('" + endpointInput + "') FORMAT Native"

// endpointBlock is one insert's worth of rollup rows, held column by column. It
// is reused across flushes: resetting keeps the capacity.
type endpointBlock struct {
	tenantID      proto.ColStr
	retentionDays proto.ColUInt16
	projectID     proto.ColStr
	serviceName   proto.ColStr
	spanKind      proto.ColStr
	spanName      proto.ColStr
	minute        proto.ColDateTime
	requestCount  proto.ColUInt64
	errorCount    proto.ColUInt64
	latencyMS     *proto.ColArr[float64]

	columns proto.Input
}

func newEndpointBlock() *endpointBlock {
	b := &endpointBlock{latencyMS: proto.NewArray[float64](new(proto.ColFloat64))}
	b.columns = proto.Input{
		{Name: "tenant_id", Data: &b.tenantID},
		{Name: "derived_retention_days", Data: &b.retentionDays},
		{Name: "project_id", Data: &b.projectID},
		{Name: "service_name", Data: &b.serviceName},
		{Name: "span_kind", Data: &b.spanKind},
		{Name: "span_name", Data: &b.spanName},
		{Name: "minute", Data: &b.minute},
		{Name: "request_count", Data: &b.requestCount},
		{Name: "error_count", Data: &b.errorCount},
		{Name: "latency_ms", Data: b.latencyMS},
	}
	return b
}

func (b *endpointBlock) append(stat EndpointStat) {
	b.tenantID.Append(stat.TenantID)
	b.retentionDays.Append(stat.RetentionDays)
	b.projectID.Append(stat.ProjectID)
	b.serviceName.Append(stat.ServiceName)
	b.spanKind.Append(stat.SpanKind)
	b.spanName.Append(stat.SpanName)
	b.minute.Append(stat.Minute)
	b.requestCount.Append(stat.RequestCount)
	b.errorCount.Append(stat.ErrorCount)
	b.latencyMS.Append(stat.LatencyMS)
}

func (b *endpointBlock) rows() int { return b.tenantID.Rows() }

func (b *endpointBlock) reset() { b.columns.Reset() }

// deduplicationToken is what makes a retried insert recognisable as one. The
// statement reads from `input()`, and that form is not covered by the store's
// own block hashing — see the insertdedup package, where it is measured.
func deduplicationToken(stats []EndpointStat) string {
	digest := insertdedup.NewDigest()
	for _, stat := range stats {
		digest.AddRow(stat.Minute.UTC(), stat.TenantID, stat.ProjectID, stat.RetentionDays,
			stat.ServiceName, stat.SpanKind, stat.SpanName,
			stat.RequestCount, stat.ErrorCount, stat.LatencyMS)
	}
	return digest.Token()
}

// EndpointWriter inserts the derived endpoint rollup into ClickHouse.
type EndpointWriter struct {
	pool  *chpool.Pool
	block *endpointBlock
}

// NewEndpointWriter returns a writer over an existing pool. The caller owns the
// pool.
func NewEndpointWriter(pool *chpool.Pool) *EndpointWriter {
	return &EndpointWriter{pool: pool, block: newEndpointBlock()}
}

// Insert writes the rows as one columnar batch. It returns an error rather than
// retrying: the caller holds the Kafka offsets and commits only once this has
// succeeded, so a failure is retried by re-consuming.
func (w *EndpointWriter) Insert(ctx context.Context, stats []EndpointStat) error {
	if len(stats) == 0 {
		return nil
	}
	w.block.reset()
	for _, stat := range stats {
		w.block.append(stat)
	}

	query := ch.Query{
		Body:     endpointInsert,
		Input:    w.block.columns,
		Settings: []ch.Setting{{Key: insertdedup.Setting, Value: deduplicationToken(stats)}},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return fmt.Errorf("insert %d endpoint rollup rows: %w", w.block.rows(), err)
	}
	return nil
}
