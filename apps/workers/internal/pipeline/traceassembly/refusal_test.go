package traceassembly

import (
	"context"
	"errors"
	"log/slog"
	"slices"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

func rowsNamed(n int) []spanRow {
	rows := make([]spanRow, n)
	for i := range rows {
		rows[i] = spanRow{SpanName: "ok", TenantID: "t", ProjectID: "p"}
	}
	return rows
}

// store stands in for ClickHouse: it refuses any insert holding a poisoned row,
// whole, as ClickHouse does, and records what it accepted.
type store struct {
	written int
	inserts int
	outage  bool
}

func (s *store) write(_ context.Context, rows []spanRow) error {
	s.inserts++
	if s.outage {
		return errors.New("packet: uvarint: read: read: EOF")
	}
	if slices.ContainsFunc(rows, func(r spanRow) bool { return r.SpanName == "poison" }) {
		return consume.AsPermanent(errors.New("INCORRECT_DATA (117)"))
	}
	s.written += len(rows)
	return nil
}

func TestOneRefusedSpanCostsOnlyItself(t *testing.T) {
	rows := rowsNamed(1000)
	rows[613].SpanName = "poison"
	clickhouse := &store{}
	var parked []spanRow
	park := func(_ context.Context, row spanRow, _ error) error {
		parked = append(parked, row)
		return nil
	}

	err := salvageRows(context.Background(), rows, consume.AsPermanent(errors.New("refused")),
		clickhouse.write, park)

	if err != nil {
		t.Fatalf("salvage returned %v; a refusal must not end the pipeline", err)
	}
	if clickhouse.written != 999 {
		t.Errorf("stored %d spans, want the 999 the store accepts", clickhouse.written)
	}
	if len(parked) != 1 || parked[0].SpanName != "poison" {
		t.Fatalf("parked %v, want exactly the poisoned span", parked)
	}
	if clickhouse.inserts > 2*10+2 {
		t.Errorf("%d inserts for one bad row in 1,000; halving should need about 2·log₂(n)", clickhouse.inserts)
	}
}

// An outage mid-salvage is still an outage: the pipeline ends and the batch is
// re-consumed, as it always was.
func TestAnOutageDuringSalvageStillEndsThePipeline(t *testing.T) {
	err := salvageRows(context.Background(), rowsNamed(4), consume.AsPermanent(errors.New("refused")),
		(&store{outage: true}).write,
		func(context.Context, spanRow, error) error { return nil })
	if err == nil || consume.IsPermanent(err) {
		t.Fatalf("err = %v, want the outage returned", err)
	}
}

func TestARefusedDerivedInsertIsCountedAndSteppedOver(t *testing.T) {
	p := &Pipeline{log: slog.New(slog.DiscardHandler)}

	err := p.stepOverRefusal(context.Background(), "metrics_sum", 7,
		consume.AsPermanent(errors.New("INCORRECT_DATA (117)")))

	if err != nil {
		t.Fatalf("err = %v; a refused derived row must not stop the settlement", err)
	}
	if p.derivedRowsRefused != 7 {
		t.Errorf("derived_rows_refused = %d, want 7 — a silent drop is the thing to avoid", p.derivedRowsRefused)
	}
	outage := errors.New("dial tcp: connection refused")
	if got := p.stepOverRefusal(context.Background(), "metrics_sum", 7, outage); !errors.Is(got, outage) {
		t.Errorf("an outage was stepped over: %v", got)
	}
}
