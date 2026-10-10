package metricwriting

import (
	"context"
	"errors"
	"slices"
	"testing"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// poisonCount marks the one histogram the fake store refuses.
const poisonCount = 7777

// metricStore stands in for ClickHouse across the five tables, refusing any
// histogram insert that holds the poisoned row and counting what each table took.
type metricStore struct {
	written [tableCount]int
}

func (s *metricStore) take(table int, subset []heldMetric) error {
	if table == tableHistogram {
		for _, h := range subset {
			if slices.ContainsFunc(h.rows.histogram, func(r histogramRow) bool { return r.Count == poisonCount }) {
				return consume.AsPermanent(errors.New("INCORRECT_DATA (117)"))
			}
		}
	}
	for _, h := range subset {
		s.written[table] += h.rows.count(table)
	}
	return nil
}

func TestARefusedMetricTableParksOnlyTheRefusedRecordAndWritesNothingTwice(t *testing.T) {
	var held []heldMetric
	for i := range 8 {
		r := rows{
			gauge:     []gaugeRow{{}, {}},
			histogram: []histogramRow{{}},
			summary:   []summaryRow{{}},
		}
		if i == 5 {
			r.histogram = []histogramRow{{Count: poisonCount}}
		}
		held = append(held, heldMetric{record: &kgo.Record{Offset: int64(i)}, rows: r})
	}
	store := &metricStore{}
	var parked []int64

	records, unstored, err := writeTables(context.Background(), held,
		func(_ context.Context, table int) error { return store.take(table, held) },
		func(_ context.Context, table int, subset []heldMetric) error { return store.take(table, subset) },
		func(_ context.Context, record *kgo.Record, _ error) error {
			parked = append(parked, record.Offset)
			return nil
		})

	if err != nil {
		t.Fatalf("err = %v; a refusal must not end the pipeline", err)
	}
	if !slices.Equal(parked, []int64{5}) || records != 1 {
		t.Fatalf("parked %v; want only offset 5", parked)
	}
	// The gauge table was written for the whole chunk before the histogram
	// refused, and must not be written again while narrowing.
	if store.written[tableGauge] != 16 {
		t.Errorf("gauge rows written %d, want each record's two exactly once (16)", store.written[tableGauge])
	}
	if store.written[tableHistogram] != 7 || store.written[tableSummary] != 7 {
		t.Errorf("histogram %d, summary %d; want the seven healthy records' rows (7 each)",
			store.written[tableHistogram], store.written[tableSummary])
	}
	// Its histogram and summary rows were not stored; its gauge rows were.
	if unstored != 2 {
		t.Errorf("unstored = %d, want the parked record's histogram and summary rows (2)", unstored)
	}
}
