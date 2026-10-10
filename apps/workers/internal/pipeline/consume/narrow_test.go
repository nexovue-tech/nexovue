package consume

import (
	"context"
	"errors"
	"slices"
	"testing"
)

// store stands in for ClickHouse: it refuses any insert holding a poisoned
// item, whole, as ClickHouse does, and records what it accepted.
type store struct {
	written []int
	inserts int
	outage  bool
}

const poisoned = -1

func (s *store) write(_ context.Context, items []int) error {
	s.inserts++
	if s.outage {
		return errors.New("packet: uvarint: read: read: EOF")
	}
	if slices.Contains(items, poisoned) {
		return AsPermanent(errors.New("INCORRECT_DATA (117)"))
	}
	s.written = append(s.written, items...)
	return nil
}

func items(n int) []int {
	out := make([]int, n)
	for i := range out {
		out[i] = i
	}
	return out
}

func TestOneRefusedItemCostsOnlyItself(t *testing.T) {
	batch := items(1000)
	batch[613] = poisoned
	clickhouse := &store{}
	var parked []int

	err := Narrow(context.Background(), batch, AsPermanent(errors.New("refused")), clickhouse.write,
		func(_ context.Context, item int, _ error) error {
			parked = append(parked, item)
			return nil
		})

	if err != nil {
		t.Fatalf("Narrow returned %v; a refusal must not end the pipeline", err)
	}
	if len(clickhouse.written) != 999 {
		t.Errorf("stored %d items, want the 999 the store accepts", len(clickhouse.written))
	}
	if !slices.Equal(parked, []int{poisoned}) {
		t.Fatalf("parked %v, want exactly the poisoned item", parked)
	}
	if clickhouse.inserts > 2*10+2 {
		t.Errorf("%d inserts for one bad item in 1,000; halving should need about 2·log₂(n)", clickhouse.inserts)
	}
}

func TestTwoRefusedItemsAreBothFoundAndNothingElseIsParked(t *testing.T) {
	batch := items(64)
	batch[3], batch[50] = poisoned, poisoned
	clickhouse := &store{}
	parked := 0

	err := Narrow(context.Background(), batch, AsPermanent(errors.New("refused")), clickhouse.write,
		func(context.Context, int, error) error { parked++; return nil })

	if err != nil || parked != 2 || len(clickhouse.written) != 62 {
		t.Fatalf("err %v, parked %d, written %d; want nil, 2, 62", err, parked, len(clickhouse.written))
	}
}

// An outage mid-narrowing is still an outage: the pipeline ends and the batch
// is re-consumed, as it always was.
func TestAnOutageDuringNarrowingStillEndsThePipeline(t *testing.T) {
	err := Narrow(context.Background(), items(4), AsPermanent(errors.New("refused")),
		(&store{outage: true}).write,
		func(context.Context, int, error) error { return nil })
	if err == nil || IsPermanent(err) {
		t.Fatalf("err = %v, want the outage returned", err)
	}
}
