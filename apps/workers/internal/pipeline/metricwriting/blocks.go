package metricwriting

import (
	"time"

	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/otlpvalue"
)

// Column names shared by more than one of the five blocks. Named once so a
// rename cannot reach four tables and miss the fifth.
const (
	columnAggregationTemporality = "aggregation_temporality"
	columnCount                  = "count"
	columnSum                    = "sum"
)

// The insert targets, named once so each statement and its column list cannot
// drift apart. Five tables because the five OTLP point shapes genuinely do not
// share a row.
const (
	gaugeTable        = "metrics_gauge"
	sumTable          = "metrics_sum"
	histogramTable    = "metrics_histogram"
	expHistogramTable = "metrics_exp_histogram"
	summaryTable      = "metrics_summary"
)

// The tables declare DateTime64(3); the column has to carry the same precision
// or ch-go tells the server the wrong type.
const timePrecision = proto.PrecisionMilli

// sharedColumns are the columns every metric table declares, held once so the
// five blocks cannot disagree about them. `series_hash` is MATERIALIZED from
// `attrs` and is never written.
type sharedColumns struct {
	timestamp      proto.ColDateTime64
	startTimestamp proto.ColDateTime64
	receivedAt     proto.ColDateTime64
	retentionDays  proto.ColUInt16
	tenantID       *proto.ColLowCardinality[string]
	projectID      *proto.ColLowCardinality[string]
	serviceName    *proto.ColLowCardinality[string]
	scopeName      *proto.ColLowCardinality[string]
	scopeVersion   *proto.ColLowCardinality[string]
	metricName     *proto.ColLowCardinality[string]
	metricUnit     *proto.ColLowCardinality[string]
	metricDesc     proto.ColStr
	resourceAttrs  proto.ColJSONStr
	attrs          proto.ColJSONStr
	flags          proto.ColUInt32
}

func newSharedColumns() *sharedColumns {
	shared := &sharedColumns{
		tenantID:     new(proto.ColStr).LowCardinality(),
		projectID:    new(proto.ColStr).LowCardinality(),
		serviceName:  new(proto.ColStr).LowCardinality(),
		scopeName:    new(proto.ColStr).LowCardinality(),
		scopeVersion: new(proto.ColStr).LowCardinality(),
		metricName:   new(proto.ColStr).LowCardinality(),
		metricUnit:   new(proto.ColStr).LowCardinality(),
	}
	shared.timestamp.WithPrecision(timePrecision)
	shared.startTimestamp.WithPrecision(timePrecision)
	shared.receivedAt.WithPrecision(timePrecision)
	return shared
}

// input returns the shared columns in the order every table declares them, so
// the per-table columns can simply be appended.
func (s *sharedColumns) input() proto.Input {
	return proto.Input{
		{Name: "timestamp", Data: &s.timestamp},
		{Name: "start_timestamp", Data: &s.startTimestamp},
		{Name: "received_at", Data: &s.receivedAt},
		{Name: "retention_days", Data: &s.retentionDays},
		{Name: "tenant_id", Data: s.tenantID},
		{Name: "project_id", Data: s.projectID},
		{Name: "service_name", Data: s.serviceName},
		{Name: "scope_name", Data: s.scopeName},
		{Name: "scope_version", Data: s.scopeVersion},
		{Name: "metric_name", Data: s.metricName},
		{Name: "metric_unit", Data: s.metricUnit},
		{Name: "metric_description", Data: &s.metricDesc},
		{Name: "resource_attrs", Data: &s.resourceAttrs},
		{Name: "attrs", Data: &s.attrs},
		{Name: "flags", Data: &s.flags},
	}
}

func (s *sharedColumns) append(row point) {
	s.timestamp.Append(row.Timestamp)
	s.startTimestamp.Append(row.StartTimestamp)
	s.receivedAt.Append(row.ReceivedAt)
	s.retentionDays.Append(row.RetentionDays)
	s.tenantID.Append(row.TenantID)
	s.projectID.Append(row.ProjectID)
	s.serviceName.Append(row.ServiceName)
	s.scopeName.Append(row.ScopeName)
	s.scopeVersion.Append(row.ScopeVersion)
	s.metricName.Append(row.MetricName)
	s.metricUnit.Append(row.MetricUnit)
	s.metricDesc.Append(row.MetricDescription)
	s.resourceAttrs.Append(row.ResourceAttrs)
	s.attrs.Append(row.Attrs)
	s.flags.Append(row.Flags)
}

// exemplarColumns are the Nested group, which ClickHouse stores as four
// parallel Array columns named `exemplars.*`.
type exemplarColumns struct {
	timestamp *proto.ColArr[time.Time]
	value     *proto.ColArr[float64]
	traceID   *proto.ColArr[[]byte]
	spanID    *proto.ColArr[[]byte]
}

func newExemplarColumns() *exemplarColumns {
	timestamps := new(proto.ColDateTime64)
	timestamps.WithPrecision(timePrecision)
	return &exemplarColumns{
		timestamp: timestamps.Array(),
		value:     new(proto.ColFloat64).Array(),
		traceID:   (&proto.ColFixedStr{Size: otlpvalue.TraceIDHexLen}).Array(),
		spanID:    (&proto.ColFixedStr{Size: otlpvalue.SpanIDHexLen}).Array(),
	}
}

func (e *exemplarColumns) input() proto.Input {
	return proto.Input{
		{Name: "exemplars.timestamp", Data: e.timestamp},
		{Name: "exemplars.value", Data: e.value},
		{Name: "exemplars.trace_id", Data: e.traceID},
		{Name: "exemplars.span_id", Data: e.spanID},
	}
}

func (e *exemplarColumns) append(row exemplars) {
	e.timestamp.Append(row.Timestamps)
	e.value.Append(row.Values)
	e.traceID.Append(row.TraceIDs)
	e.spanID.Append(row.SpanIDs)
}

// gaugeBlock is one insert's worth of `metrics_gauge`. A block is reused across
// flushes: resetting keeps the capacity, so a steady stream stops allocating.
type gaugeBlock struct {
	shared    *sharedColumns
	value     proto.ColFloat64
	exemplars *exemplarColumns

	columns proto.Input
}

func newGaugeBlock() *gaugeBlock {
	block := &gaugeBlock{shared: newSharedColumns(), exemplars: newExemplarColumns()}
	block.columns = joinColumns(
		block.shared.input(),
		proto.Input{{Name: "value", Data: &block.value}},
		block.exemplars.input(),
	)
	return block
}

func (b *gaugeBlock) append(row gaugeRow) {
	b.shared.append(row.point)
	b.value.Append(row.Value)
	b.exemplars.append(row.Exemplars)
}

func (b *gaugeBlock) rows() int     { return b.shared.timestamp.Rows() }
func (b *gaugeBlock) reset()        { b.columns.Reset() }
func (b *gaugeBlock) table() string { return gaugeTable }

// sumBlock is one insert's worth of `metrics_sum`.
type sumBlock struct {
	shared      *sharedColumns
	value       proto.ColFloat64
	temporality *proto.ColLowCardinality[string]
	monotonic   proto.ColBool
	exemplars   *exemplarColumns

	columns proto.Input
}

func newSumBlock() *sumBlock {
	block := &sumBlock{
		shared:      newSharedColumns(),
		temporality: new(proto.ColStr).LowCardinality(),
		exemplars:   newExemplarColumns(),
	}
	block.columns = joinColumns(
		block.shared.input(),
		proto.Input{
			{Name: "value", Data: &block.value},
			{Name: columnAggregationTemporality, Data: block.temporality},
			{Name: "is_monotonic", Data: &block.monotonic},
		},
		block.exemplars.input(),
	)
	return block
}

func (b *sumBlock) append(row sumRow) {
	b.shared.append(row.point)
	b.value.Append(row.Value)
	b.temporality.Append(row.Temporality)
	b.monotonic.Append(row.Monotonic)
	b.exemplars.append(row.Exemplars)
}

func (b *sumBlock) rows() int     { return b.shared.timestamp.Rows() }
func (b *sumBlock) reset()        { b.columns.Reset() }
func (b *sumBlock) table() string { return sumTable }

// histogramBlock is one insert's worth of `metrics_histogram`.
type histogramBlock struct {
	shared         *sharedColumns
	count          proto.ColUInt64
	sum            *proto.ColNullable[float64]
	bucketCounts   *proto.ColArr[uint64]
	explicitBounds *proto.ColArr[float64]
	min            *proto.ColNullable[float64]
	max            *proto.ColNullable[float64]
	temporality    *proto.ColLowCardinality[string]
	exemplars      *exemplarColumns

	columns proto.Input
}

func newHistogramBlock() *histogramBlock {
	block := &histogramBlock{
		shared:         newSharedColumns(),
		sum:            proto.NewColNullable[float64](new(proto.ColFloat64)),
		bucketCounts:   new(proto.ColUInt64).Array(),
		explicitBounds: new(proto.ColFloat64).Array(),
		min:            proto.NewColNullable[float64](new(proto.ColFloat64)),
		max:            proto.NewColNullable[float64](new(proto.ColFloat64)),
		temporality:    new(proto.ColStr).LowCardinality(),
		exemplars:      newExemplarColumns(),
	}
	block.columns = joinColumns(
		block.shared.input(),
		proto.Input{
			{Name: columnCount, Data: &block.count},
			{Name: columnSum, Data: block.sum},
			{Name: "bucket_counts", Data: block.bucketCounts},
			{Name: "explicit_bounds", Data: block.explicitBounds},
			{Name: "min", Data: block.min},
			{Name: "max", Data: block.max},
			{Name: columnAggregationTemporality, Data: block.temporality},
		},
		block.exemplars.input(),
	)
	return block
}

func (b *histogramBlock) append(row histogramRow) {
	b.shared.append(row.point)
	b.count.Append(row.Count)
	b.sum.Append(optionalFloat(row.Sum))
	b.bucketCounts.Append(row.BucketCounts)
	b.explicitBounds.Append(row.ExplicitBounds)
	b.min.Append(optionalFloat(row.Min))
	b.max.Append(optionalFloat(row.Max))
	b.temporality.Append(row.Temporality)
	b.exemplars.append(row.Exemplars)
}

func (b *histogramBlock) rows() int     { return b.shared.timestamp.Rows() }
func (b *histogramBlock) reset()        { b.columns.Reset() }
func (b *histogramBlock) table() string { return histogramTable }

// expHistogramBlock is one insert's worth of `metrics_exp_histogram`.
type expHistogramBlock struct {
	shared         *sharedColumns
	count          proto.ColUInt64
	sum            *proto.ColNullable[float64]
	scale          proto.ColInt32
	zeroCount      proto.ColUInt64
	positiveOffset proto.ColInt32
	positiveCounts *proto.ColArr[uint64]
	negativeOffset proto.ColInt32
	negativeCounts *proto.ColArr[uint64]
	min            *proto.ColNullable[float64]
	max            *proto.ColNullable[float64]
	temporality    *proto.ColLowCardinality[string]
	exemplars      *exemplarColumns

	columns proto.Input
}

func newExpHistogramBlock() *expHistogramBlock {
	block := &expHistogramBlock{
		shared:         newSharedColumns(),
		sum:            proto.NewColNullable[float64](new(proto.ColFloat64)),
		positiveCounts: new(proto.ColUInt64).Array(),
		negativeCounts: new(proto.ColUInt64).Array(),
		min:            proto.NewColNullable[float64](new(proto.ColFloat64)),
		max:            proto.NewColNullable[float64](new(proto.ColFloat64)),
		temporality:    new(proto.ColStr).LowCardinality(),
		exemplars:      newExemplarColumns(),
	}
	block.columns = joinColumns(
		block.shared.input(),
		proto.Input{
			{Name: columnCount, Data: &block.count},
			{Name: columnSum, Data: block.sum},
			{Name: "scale", Data: &block.scale},
			{Name: "zero_count", Data: &block.zeroCount},
			{Name: "positive_offset", Data: &block.positiveOffset},
			{Name: "positive_bucket_counts", Data: block.positiveCounts},
			{Name: "negative_offset", Data: &block.negativeOffset},
			{Name: "negative_bucket_counts", Data: block.negativeCounts},
			{Name: "min", Data: block.min},
			{Name: "max", Data: block.max},
			{Name: columnAggregationTemporality, Data: block.temporality},
		},
		block.exemplars.input(),
	)
	return block
}

func (b *expHistogramBlock) append(row expHistogramRow) {
	b.shared.append(row.point)
	b.count.Append(row.Count)
	b.sum.Append(optionalFloat(row.Sum))
	b.scale.Append(row.Scale)
	b.zeroCount.Append(row.ZeroCount)
	b.positiveOffset.Append(row.PositiveOffset)
	b.positiveCounts.Append(row.PositiveBucketCounts)
	b.negativeOffset.Append(row.NegativeOffset)
	b.negativeCounts.Append(row.NegativeBucketCounts)
	b.min.Append(optionalFloat(row.Min))
	b.max.Append(optionalFloat(row.Max))
	b.temporality.Append(row.Temporality)
	b.exemplars.append(row.Exemplars)
}

func (b *expHistogramBlock) rows() int     { return b.shared.timestamp.Rows() }
func (b *expHistogramBlock) reset()        { b.columns.Reset() }
func (b *expHistogramBlock) table() string { return expHistogramTable }

// summaryBlock is one insert's worth of `metrics_summary`. A summary carries
// neither temporality nor exemplars: OTLP defines neither for it.
type summaryBlock struct {
	shared         *sharedColumns
	count          proto.ColUInt64
	sum            proto.ColFloat64
	quantiles      *proto.ColArr[float64]
	quantileValues *proto.ColArr[float64]

	columns proto.Input
}

func newSummaryBlock() *summaryBlock {
	block := &summaryBlock{
		shared:         newSharedColumns(),
		quantiles:      new(proto.ColFloat64).Array(),
		quantileValues: new(proto.ColFloat64).Array(),
	}
	block.columns = joinColumns(
		block.shared.input(),
		proto.Input{
			{Name: columnCount, Data: &block.count},
			{Name: columnSum, Data: &block.sum},
			{Name: "quantiles.quantile", Data: block.quantiles},
			{Name: "quantiles.value", Data: block.quantileValues},
		},
	)
	return block
}

func (b *summaryBlock) append(row summaryRow) {
	b.shared.append(row.point)
	b.count.Append(row.Count)
	b.sum.Append(row.Sum)
	b.quantiles.Append(row.Quantiles)
	b.quantileValues.Append(row.QuantileValues)
}

func (b *summaryBlock) rows() int     { return b.shared.timestamp.Rows() }
func (b *summaryBlock) reset()        { b.columns.Reset() }
func (b *summaryBlock) table() string { return summaryTable }

// optionalFloat keeps an absent OTLP field absent. A histogram with no sum and
// one summing to zero are different facts, and the column is Nullable so that
// the difference survives the write.
func optionalFloat(value *float64) proto.Nullable[float64] {
	if value == nil {
		return proto.Null[float64]()
	}
	return proto.NewNullable(*value)
}

func joinColumns(parts ...proto.Input) proto.Input {
	var all proto.Input
	for _, part := range parts {
		all = append(all, part...)
	}
	return all
}
