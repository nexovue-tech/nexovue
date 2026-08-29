package derive

import (
	"context"
	"fmt"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// metricsTable is the insert target, named once so the statement and the column
// list cannot drift apart.
const metricsTable = "metrics_sum"

// Every counter here is a per-minute bucket of a quantity that only accumulates
// within its window, which is OTLP's delta sum exactly (ADR 0066). Stating both
// is what lets a reader turn these rows into a rate without guessing.
const (
	deltaTemporality = "DELTA"
	monotonicSum     = true
)

// jsonAsStringSetting lets the JSON column travel the native protocol as one
// JSON document per row, which is the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// block is one insert's worth of derived points, held column by column. It is
// reused across flushes: resetting keeps the capacity.
type block struct {
	timestamp     proto.ColDateTime64
	startAt       proto.ColDateTime64
	retentionDays proto.ColUInt16
	tenantID      *proto.ColLowCardinality[string]
	projectID     *proto.ColLowCardinality[string]
	metricName    *proto.ColLowCardinality[string]
	value         proto.ColFloat64
	serviceName   *proto.ColLowCardinality[string]
	attrs         proto.ColJSONStr
	temporality   *proto.ColLowCardinality[string]
	monotonic     proto.ColBool

	columns proto.Input
}

func newBlock() *block {
	b := &block{
		tenantID:    new(proto.ColStr).LowCardinality(),
		projectID:   new(proto.ColStr).LowCardinality(),
		metricName:  new(proto.ColStr).LowCardinality(),
		serviceName: new(proto.ColStr).LowCardinality(),
		temporality: new(proto.ColStr).LowCardinality(),
	}
	// The column carries its own precision; the table declares DateTime64(3).
	b.timestamp.WithPrecision(proto.PrecisionMilli)
	b.startAt.WithPrecision(proto.PrecisionMilli)

	b.columns = proto.Input{
		{Name: "timestamp", Data: &b.timestamp},
		{Name: "start_timestamp", Data: &b.startAt},
		{Name: "retention_days", Data: &b.retentionDays},
		{Name: "tenant_id", Data: b.tenantID},
		{Name: "project_id", Data: b.projectID},
		{Name: "metric_name", Data: b.metricName},
		{Name: "value", Data: &b.value},
		{Name: "service_name", Data: b.serviceName},
		{Name: "attrs", Data: &b.attrs},
		{Name: "aggregation_temporality", Data: b.temporality},
		{Name: "is_monotonic", Data: &b.monotonic},
	}
	return b
}

func (b *block) append(point Point) {
	start, end := point.interval()
	b.timestamp.Append(end)
	b.startAt.Append(start)
	b.retentionDays.Append(point.RetentionDays)
	b.tenantID.Append(point.TenantID)
	b.projectID.Append(point.ProjectID)
	b.metricName.Append(point.MetricName)
	b.value.Append(point.Value)
	b.serviceName.Append(point.ServiceName)
	b.attrs.Append(point.Attrs)
	b.temporality.Append(deltaTemporality)
	b.monotonic.Append(monotonicSum)
}

func (b *block) rows() int { return b.timestamp.Rows() }

func (b *block) reset() { b.columns.Reset() }

// Writer inserts derived points into ClickHouse.
type Writer struct {
	pool  *chpool.Pool
	block *block
}

// NewWriter returns a writer over an existing pool. The caller owns the pool.
func NewWriter(pool *chpool.Pool) *Writer {
	return &Writer{pool: pool, block: newBlock()}
}

// Insert writes the points as one columnar batch. It returns an error rather
// than retrying: the caller holds the Kafka offsets and commits only once this
// has succeeded, so a failure is retried by re-consuming.
func (w *Writer) Insert(ctx context.Context, points []Point) error {
	if len(points) == 0 {
		return nil
	}
	w.block.reset()
	for _, point := range points {
		w.block.append(point)
	}

	query := ch.Query{
		Body:  w.block.columns.Into(metricsTable),
		Input: w.block.columns,
		Settings: []ch.Setting{
			{Key: jsonAsStringSetting, Value: "1"},
		},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return fmt.Errorf("insert %d metric points: %w", w.block.rows(), err)
	}
	return nil
}
