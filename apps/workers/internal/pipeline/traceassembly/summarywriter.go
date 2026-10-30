package traceassembly

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/storefailure"
)

// summariesTable is the insert target, named once so the statement and the
// column list cannot drift apart.
const summariesTable = "trace_summaries"

// summaryBlock is one insert's worth of trace summaries held column by column.
// Reused across flushes: resetting keeps the capacity.
//
// Every measure is a `SimpleAggregateFunction`, which stores its own underlying
// type rather than a serialised aggregate state — so unlike the endpoint and
// edge rollups this insert carries its data as plain columns and needs no
// `input(…)` and no deduplication token.
type summaryBlock struct {
	tenantID      *proto.ColLowCardinality[string]
	projectID     *proto.ColLowCardinality[string]
	startTime     proto.ColDateTime64
	traceStart    aggregateDateTime64
	traceID       proto.ColFixedStr
	receivedAt    proto.ColDateTime64
	retentionDays proto.ColUInt16
	durationMS    proto.ColFloat64
	spanCount     proto.ColUInt32
	errorCount    proto.ColUInt32
	worstSeverity proto.ColUInt8
	rootService   *proto.ColLowCardinality[string]
	rootName      *proto.ColLowCardinality[string]
	services      *proto.ColArr[string]
	endpoints     *proto.ColArr[string]

	columns proto.Input
}

func newSummaryBlock() *summaryBlock {
	block := &summaryBlock{
		tenantID:    new(proto.ColStr).LowCardinality(),
		projectID:   new(proto.ColStr).LowCardinality(),
		traceID:     proto.ColFixedStr{Size: otlpvalue.TraceIDHexLen},
		rootService: new(proto.ColStr).LowCardinality(),
		rootName:    new(proto.ColStr).LowCardinality(),
		services:    proto.NewArray[string](new(proto.ColStr)),
		endpoints:   proto.NewArray[string](new(proto.ColStr)),
	}
	// The columns carry their own precision; without it ch-go cannot encode a
	// time and the server would be told the wrong type.
	block.startTime.WithPrecision(proto.PrecisionNano)
	block.traceStart.WithPrecision(proto.PrecisionNano)
	block.receivedAt.WithPrecision(proto.PrecisionNano)

	block.columns = proto.Input{
		{Name: "tenant_id", Data: block.tenantID},
		{Name: "project_id", Data: block.projectID},
		{Name: "start_time", Data: &block.startTime},
		{Name: "trace_start", Data: &block.traceStart},
		{Name: "trace_id", Data: &block.traceID},
		{Name: "received_at", Data: &block.receivedAt},
		{Name: "retention_days", Data: &block.retentionDays},
		{Name: "duration_ms", Data: &block.durationMS},
		{Name: "span_count", Data: &block.spanCount},
		{Name: "error_count", Data: &block.errorCount},
		{Name: "worst_severity", Data: &block.worstSeverity},
		{Name: "root_service", Data: block.rootService},
		{Name: "root_name", Data: block.rootName},
		{Name: "span_services", Data: block.services},
		{Name: "span_endpoints", Data: block.endpoints},
	}
	return block
}

func (b *summaryBlock) append(row summaryRow) {
	b.tenantID.Append(row.TenantID)
	b.projectID.Append(row.ProjectID)
	b.startTime.Append(row.StartTime)
	b.traceStart.Append(row.TraceStart)
	b.traceID.Append(row.TraceID)
	b.receivedAt.Append(row.ReceivedAt)
	b.retentionDays.Append(row.RetentionDays)
	b.durationMS.Append(row.DurationMS)
	b.spanCount.Append(row.SpanCount)
	b.errorCount.Append(row.ErrorCount)
	b.worstSeverity.Append(row.WorstSeverity)
	b.rootService.Append(row.RootService)
	b.rootName.Append(row.RootName)
	b.services.Append(row.Services)
	b.endpoints.Append(row.Endpoints)
}

func (b *summaryBlock) rows() int { return b.startTime.Rows() }

func (b *summaryBlock) reset() { b.columns.Reset() }

// statement is the INSERT this block's columns belong to.
func (b *summaryBlock) statement() string { return b.columns.Into(summariesTable) }

// aggregateDateTime64 is the column for `SimpleAggregateFunction(min,
// DateTime64(9))`. ch-go infers a DateTime64's precision from the server's type
// and cannot read it through that wrapper — it parses "min" as the precision and
// refuses the insert. The precision is set here instead, to the table's 9.
type aggregateDateTime64 struct {
	proto.ColDateTime64
}

func (c *aggregateDateTime64) Infer(proto.ColumnType) error { return nil }

// summaryWriter inserts blocks of trace summaries into ClickHouse.
type summaryWriter struct {
	pool  *chpool.Pool
	block *summaryBlock
}

func newSummaryWriter(pool *chpool.Pool) *summaryWriter {
	return &summaryWriter{pool: pool, block: newSummaryBlock()}
}

// insert writes the rows as one columnar batch. It returns an error rather than
// retrying: the caller holds the Kafka offsets and only commits once this has
// succeeded, so a failure is retried by re-consuming rather than in here.
//
// A retry that follows a lost acknowledgement is safe without any guard. Every
// row is a complete snapshot of its trace and every column merges with max, min
// or set union, so the same row written twice is the same answer — which is
// what the summing rollups need a deduplication window and a token to achieve.
func (w *summaryWriter) insert(ctx context.Context, rows []summaryRow) error {
	if len(rows) == 0 {
		return nil
	}
	w.block.reset()
	for _, row := range rows {
		w.block.append(row)
	}

	query := ch.Query{Body: w.block.statement(), Input: w.block.columns}
	if err := w.pool.Do(ctx, query); err != nil {
		return storefailure.Judged(fmt.Errorf("insert %d trace summaries: %w", len(rows), err))
	}
	return nil
}
