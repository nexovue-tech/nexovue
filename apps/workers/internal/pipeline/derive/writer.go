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
const metricsTable = "metrics"

// jsonAsStringSetting lets the JSON column travel the native protocol as one
// JSON document per row, which is the shape ch-go's ColJSONStr encodes.
const jsonAsStringSetting = "output_format_native_write_json_as_string"

// block is one insert's worth of derived points, held column by column. It is
// reused across flushes: resetting keeps the capacity.
type block struct {
	timestamp   proto.ColDateTime64
	tenantID    *proto.ColLowCardinality[string]
	projectID   *proto.ColLowCardinality[string]
	metricName  *proto.ColLowCardinality[string]
	value       proto.ColFloat64
	serviceName *proto.ColLowCardinality[string]
	attrs       proto.ColJSONStr

	columns proto.Input
}

func newBlock() *block {
	b := &block{
		tenantID:    new(proto.ColStr).LowCardinality(),
		projectID:   new(proto.ColStr).LowCardinality(),
		metricName:  new(proto.ColStr).LowCardinality(),
		serviceName: new(proto.ColStr).LowCardinality(),
	}
	// The column carries its own precision; the table declares DateTime64(3).
	b.timestamp.WithPrecision(proto.PrecisionMilli)

	b.columns = proto.Input{
		{Name: "timestamp", Data: &b.timestamp},
		{Name: "tenant_id", Data: b.tenantID},
		{Name: "project_id", Data: b.projectID},
		{Name: "metric_name", Data: b.metricName},
		{Name: "value", Data: &b.value},
		{Name: "service_name", Data: b.serviceName},
		{Name: "attrs", Data: &b.attrs},
	}
	return b
}

func (b *block) append(point Point) {
	b.timestamp.Append(point.Timestamp)
	b.tenantID.Append(point.TenantID)
	b.projectID.Append(point.ProjectID)
	b.metricName.Append(point.MetricName)
	b.value.Append(point.Value)
	b.serviceName.Append(point.ServiceName)
	b.attrs.Append(point.Attrs)
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

// Applied reports whether a batch that may already have been written is present.
//
// It exists for one narrow case that ADR 0061 left open. If an insert lands and
// only its acknowledgement is lost, the retry writes the identical block again.
// ClickHouse would normally recognise the repeat and drop it, but that
// deduplication log does not survive a restart of ClickHouse — and a restart is
// exactly what tends to break the acknowledgement in the first place.
//
// A block is written atomically, so one point answers for the whole batch.
func (w *Writer) Applied(ctx context.Context, point Point) (bool, error) {
	var found proto.ColUInt64
	query := ch.Query{
		Body: "SELECT count() FROM " + metricsTable + " WHERE " +
			"timestamp = toDateTime64({minute:String}, 3) " +
			"AND tenant_id = {tenant:String} AND project_id = {project:String} " +
			"AND metric_name = {metric:String} AND service_name = {service:String}",
		Parameters: ch.Parameters(map[string]any{
			"minute":  point.Timestamp.UTC().Format("2006-01-02 15:04:05.000"),
			"tenant":  point.TenantID,
			"project": point.ProjectID,
			"metric":  point.MetricName,
			"service": point.ServiceName,
		}),
		Result: proto.Results{{Name: "count()", Data: &found}},
	}
	if err := w.pool.Do(ctx, query); err != nil {
		return false, fmt.Errorf("checking whether metric points landed: %w", err)
	}
	return found.Row(0) > 0, nil
}
