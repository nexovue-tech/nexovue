package metricwriting

import "time"

// point is what every metric row carries whatever its shape: who sent it, when,
// what the metric is called, and the attributes that identify its series.
//
// Nothing here is derived or converted. A point is written with the temporality,
// start time and flags the SDK sent, because none of them can be recovered later
// and a guess baked into stored data is not recoverable at all.
type point struct {
	// Timestamp is the client's own clock: when the point was recorded.
	Timestamp time.Time
	// StartTimestamp is the beginning of the interval the point covers. For a
	// cumulative series it is what tells a reader a counter reset from a
	// counter that fell, so it is stored even when the SDK left it zero.
	StartTimestamp time.Time
	// ReceivedAt is our own clock. The row partitions and expires on it, so a
	// client's wrong clock cannot pin a row in storage or delete it early.
	ReceivedAt time.Time
	// RetentionDays is the project's derived-signal retention, in the partition
	// key so a part holds one retention and drops whole.
	RetentionDays uint16

	TenantID  string
	ProjectID string

	ServiceName  string
	ScopeName    string
	ScopeVersion string

	MetricName        string
	MetricUnit        string
	MetricDescription string

	ResourceAttrs string
	Attrs         string

	// Flags is OTLP's DataPointFlags, stored raw. Bit 0 is NO_RECORDED_VALUE:
	// the point is a gap marker and its value means nothing. The row is still
	// written — the gap is the fact — and the flag is how a reader tells a gap
	// from a zero.
	Flags uint32

	Exemplars exemplars
}

// exemplars is the Nested group as ClickHouse stores it: four parallel arrays,
// one row's worth. An exemplar is the only link from a metric point back to a
// trace, which is why they are stored at all.
type exemplars struct {
	Timestamps []time.Time
	Values     []float64
	// TraceIDs and SpanIDs are hex-encoded and padded to the FixedString widths
	// the columns declare.
	TraceIDs [][]byte
	SpanIDs  [][]byte
}

// gaugeRow is one `metrics_gauge` row. A gauge is a sampled value with no
// temporality: there is no interval to accumulate over.
type gaugeRow struct {
	point
	Value float64
}

// sumRow is one `metrics_sum` row. Temporality and monotonicity are the two
// facts that make a sum readable, and neither can be inferred from the values.
type sumRow struct {
	point
	Value       float64
	Temporality string
	Monotonic   bool
}

// histogramRow is one `metrics_histogram` row.
//
// Sum, Min and Max are pointers because OTLP marks them optional: a histogram
// with no sum and one summing to zero are different facts, and flattening the
// first into the second invents data.
type histogramRow struct {
	point
	Count          uint64
	Sum            *float64
	BucketCounts   []uint64
	ExplicitBounds []float64
	Min            *float64
	Max            *float64
	Temporality    string
}

// expHistogramRow is one `metrics_exp_histogram` row. Its buckets are described
// by a scale and two offsets rather than a bound list; the bounds are computed
// from them at read time.
type expHistogramRow struct {
	point
	Count                uint64
	Sum                  *float64
	Scale                int32
	ZeroCount            uint64
	PositiveOffset       int32
	PositiveBucketCounts []uint64
	NegativeOffset       int32
	NegativeBucketCounts []uint64
	Min                  *float64
	Max                  *float64
	Temporality          string
}

// summaryRow is one `metrics_summary` row. A summary carries neither
// temporality nor exemplars — OTLP does not define them for it — and its sum is
// required rather than optional.
type summaryRow struct {
	point
	Count          uint64
	Sum            float64
	Quantiles      []float64
	QuantileValues []float64
}

// rows is one message's or one batch's output, split by the table each point
// belongs in. One OTLP request can carry every shape at once.
type rows struct {
	gauge        []gaugeRow
	sum          []sumRow
	histogram    []histogramRow
	expHistogram []expHistogramRow
	summary      []summaryRow
}
