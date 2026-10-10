package logwriting

import (
	"context"
	"errors"
	"slices"
	"testing"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// One tenant's malformed record must not take the other records in its chunk to
// the dead-letter topic with it (docs/changes/refusals-narrowed-to-the-record).
func TestARefusedLogChunkParksOnlyTheRefusedRecord(t *testing.T) {
	var held []heldLog
	for i := range 10 {
		tenant := "healthy"
		if i == 4 {
			tenant = "poison"
		}
		held = append(held, heldLog{
			record: &kgo.Record{Offset: int64(i)},
			rows:   []logRow{{TenantID: tenant}, {TenantID: tenant}, {TenantID: tenant}},
		})
	}
	written := 0
	insert := func(_ context.Context, rows []logRow) error {
		if slices.ContainsFunc(rows, func(r logRow) bool { return r.TenantID == "poison" }) {
			return consume.AsPermanent(errors.New("INCORRECT_DATA (117)"))
		}
		written += len(rows)
		return nil
	}
	var parked []int64
	park := func(_ context.Context, record *kgo.Record, _ error) error {
		parked = append(parked, record.Offset)
		return nil
	}

	records, rows, err := narrowLogs(context.Background(), held,
		consume.AsPermanent(errors.New("refused")), insert, park)

	if err != nil {
		t.Fatalf("err = %v; a refusal must not end the pipeline", err)
	}
	if !slices.Equal(parked, []int64{4}) || records != 1 || rows != 3 {
		t.Fatalf("parked %v (%d records, %d rows); want only offset 4, with its 3 rows", parked, records, rows)
	}
	if written != 27 {
		t.Errorf("wrote %d rows, want the other nine records' 27", written)
	}
}
