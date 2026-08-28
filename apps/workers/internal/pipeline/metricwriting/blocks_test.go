package metricwriting

import (
	"strings"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go/proto"
)

// sharedTableColumns are the columns every metric table declares before its own,
// in declaration order. `series_hash` sits between `attrs` and `flags` in the
// DDL and is deliberately absent here: it is MATERIALIZED from `attrs`, and
// writing it would be rejected.
var sharedTableColumns = []string{
	"timestamp", "start_timestamp", "received_at", "retention_days",
	"tenant_id", "project_id", "service_name", "scope_name", "scope_version",
	"metric_name", "metric_unit", "metric_description",
	"resource_attrs", "attrs", "flags",
}

var exemplarTableColumns = []string{
	"exemplars.timestamp", "exemplars.value", "exemplars.trace_id", "exemplars.span_id",
}

func columnsOf(own ...[]string) []string {
	all := append([]string{}, sharedTableColumns...)
	for _, group := range own {
		all = append(all, group...)
	}
	return all
}

// The five tables, as ClickHouse declares them. A column added to a table has
// to be added here, and the block has to grow with it or this fails.
var tableColumns = map[string][]string{
	gaugeTable: columnsOf([]string{"value"}, exemplarTableColumns),
	sumTable: columnsOf(
		[]string{"value", "aggregation_temporality", "is_monotonic"}, exemplarTableColumns),
	histogramTable: columnsOf([]string{
		"count", "sum", "bucket_counts", "explicit_bounds",
		"min", "max", "aggregation_temporality",
	}, exemplarTableColumns),
	expHistogramTable: columnsOf([]string{
		"count", "sum", "scale", "zero_count",
		"positive_offset", "positive_bucket_counts",
		"negative_offset", "negative_bucket_counts",
		"min", "max", "aggregation_temporality",
	}, exemplarTableColumns),
	summaryTable: columnsOf([]string{
		"count", "sum", "quantiles.quantile", "quantiles.value",
	}),
}

// block is what every metric block does, so one test can walk all five.
type block interface {
	rows() int
	reset()
	table() string
}

// blocks pairs each block with a function that appends one representative row,
// so the shared assertions never have to know a row's shape.
type blockUnderTest struct {
	block     block
	input     proto.Input
	appendOne func()
}

func blocks() []blockUnderTest { return blocksWith(testPoint()) }

// blocksWith is blocks() over a caller-supplied base row, so a test that needs
// the rows marked can stamp them once instead of five times.
func blocksWith(base point) []blockUnderTest {
	gauge := newGaugeBlock()
	sum := newSumBlock()
	histogram := newHistogramBlock()
	expHistogram := newExpHistogramBlock()
	summary := newSummaryBlock()

	return []blockUnderTest{
		{gauge, gauge.columns, func() { gauge.append(gaugeRow{point: base}) }},
		{sum, sum.columns, func() {
			sum.append(sumRow{point: base, Temporality: "CUMULATIVE", Monotonic: true})
		}},
		{histogram, histogram.columns, func() {
			histogram.append(histogramRow{
				point:          base,
				BucketCounts:   []uint64{1, 2},
				ExplicitBounds: []float64{1.5},
				Temporality:    "DELTA",
			})
		}},
		{expHistogram, expHistogram.columns, func() {
			expHistogram.append(expHistogramRow{
				point:                base,
				PositiveBucketCounts: []uint64{3},
				NegativeBucketCounts: []uint64{4},
				Temporality:          "DELTA",
			})
		}},
		{summary, summary.columns, func() {
			summary.append(summaryRow{
				point:          base,
				Quantiles:      []float64{0.99},
				QuantileValues: []float64{12},
			})
		}},
	}
}

func TestEveryBlockCoversItsTableExactly(t *testing.T) {
	for _, subject := range blocks() {
		name := subject.block.table()
		want := tableColumns[name]

		if len(subject.input) != len(want) {
			t.Fatalf("%s block has %d columns, the table has %d",
				name, len(subject.input), len(want))
		}
		for i, column := range subject.input {
			if column.Name != want[i] {
				t.Errorf("%s column %d is %q, want %q", name, i, column.Name, want[i])
			}
		}

		statement := subject.input.Into(name)
		if !strings.HasPrefix(statement, `INSERT INTO "`+name+`"`) {
			t.Errorf("statement = %q, want it to insert into %s", statement, name)
		}
		if strings.Contains(statement, "series_hash") {
			t.Errorf("%s statement writes series_hash, which is MATERIALIZED", name)
		}
	}
}

func TestAppendAdvancesEveryColumnTogether(t *testing.T) {
	for _, subject := range blocks() {
		name := subject.block.table()
		for i := range 3 {
			subject.appendOne()
			if subject.block.rows() != i+1 {
				t.Fatalf("%s rows() = %d after %d appends", name, subject.block.rows(), i+1)
			}
			for _, column := range subject.input {
				if got := column.Data.Rows(); got != i+1 {
					t.Fatalf("%s column %q holds %d rows, want %d",
						name, column.Name, got, i+1)
				}
			}
		}
	}
}

// A block is reused between batches, so a stale row must never ride along on
// the next insert.
func TestResetEmptiesEveryColumn(t *testing.T) {
	for _, subject := range blocks() {
		name := subject.block.table()
		subject.appendOne()
		subject.block.reset()

		if subject.block.rows() != 0 {
			t.Errorf("%s rows() = %d after reset, want 0", name, subject.block.rows())
		}
		for _, column := range subject.input {
			if got := column.Data.Rows(); got != 0 {
				t.Errorf("%s column %q holds %d rows after reset, want 0",
					name, column.Name, got)
			}
		}
	}
}

// OTLP marks a histogram's sum, min and max optional, and the columns are
// Nullable so the difference survives. An absent one must arrive null, not zero:
// "no sum" and "sums to zero" are different facts.
func TestAnAbsentOptionalIsWrittenAsNullNotZero(t *testing.T) {
	block := newHistogramBlock()
	block.append(histogramRow{point: testPoint()}) // Sum, Min and Max all nil.
	block.append(histogramRow{
		point: testPoint(),
		Sum:   float64Ptr(0),
		Min:   float64Ptr(0),
		Max:   float64Ptr(0),
	})

	for name, column := range map[string]*proto.ColNullable[float64]{
		"sum": block.sum, "min": block.min, "max": block.max,
	} {
		if absent := column.Row(0); absent.Set {
			t.Errorf("%s row 0 is set to %v, want null", name, absent.Value)
		}
		present := column.Row(1)
		if !present.Set || present.Value != 0 {
			t.Errorf("%s row 1 = %+v, want a set zero", name, present)
		}
	}
}

// A gauge row and a sum row from the same point must not disagree about the
// exemplar arrays, which ClickHouse stores as a Nested group and requires to be
// the same length per row.
func TestExemplarArraysStayInStep(t *testing.T) {
	block := newGaugeBlock()
	block.append(gaugeRow{point: point{
		Timestamp: testTime, StartTimestamp: testTime, ReceivedAt: testTime,
		Attrs: emptyJSONObject, ResourceAttrs: emptyJSONObject,
		Exemplars: exemplars{
			Timestamps: []time.Time{testTime, testTime},
			Values:     []float64{1, 2},
			TraceIDs:   [][]byte{hexID(nil, traceIDHexLen), hexID(nil, traceIDHexLen)},
			SpanIDs:    [][]byte{hexID(nil, spanIDHexLen), hexID(nil, spanIDHexLen)},
		},
	}})
	block.append(gaugeRow{point: testPoint()}) // No exemplars at all.

	if got := block.exemplars.timestamp.Row(0); len(got) != 2 {
		t.Errorf("row 0 holds %d exemplar timestamps, want 2", len(got))
	}
	if got := block.exemplars.value.Row(1); len(got) != 0 {
		t.Errorf("row 1 holds %d exemplar values, want none", len(got))
	}
	if got := block.exemplars.traceID.Row(0); len(got) != 2 {
		t.Errorf("row 0 holds %d exemplar trace ids, want 2", len(got))
	}
}

var testTime = time.Unix(0, 1_700_000_000_000_000_000).UTC()

func testPoint() point {
	return point{
		Timestamp:      testTime,
		StartTimestamp: testTime,
		ReceivedAt:     testTime,
		RetentionDays:  395,
		TenantID:       "tenant-a",
		ProjectID:      "project-a",
		ServiceName:    "checkout",
		ScopeName:      "go.opentelemetry.io/otel/sdk",
		ScopeVersion:   "1.44.0",
		MetricName:     "http.server.duration",
		MetricUnit:     "ms",
		ResourceAttrs:  emptyJSONObject,
		Attrs:          emptyJSONObject,
	}
}

func float64Ptr(value float64) *float64 { return &value }
