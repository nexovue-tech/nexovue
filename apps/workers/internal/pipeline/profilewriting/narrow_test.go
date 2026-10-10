package profilewriting

import (
	"context"
	"errors"
	"slices"
	"testing"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

func TestARefusedProfileChunkParksOnlyTheRefusedRecord(t *testing.T) {
	var held []heldProfile
	for i := range 6 {
		tenant := "healthy"
		if i == 0 {
			tenant = "poison"
		}
		held = append(held, heldProfile{
			record: &kgo.Record{Offset: int64(i)},
			rows:   []row{{TenantID: tenant}, {TenantID: tenant}},
		})
	}
	written := 0
	insert := func(_ context.Context, rows []row) error {
		if slices.ContainsFunc(rows, func(r row) bool { return r.TenantID == "poison" }) {
			return consume.AsPermanent(errors.New("INCORRECT_DATA (117)"))
		}
		written += len(rows)
		return nil
	}
	var parked []int64
	records, rows, err := narrowProfiles(context.Background(), held,
		consume.AsPermanent(errors.New("refused")), insert,
		func(_ context.Context, record *kgo.Record, _ error) error {
			parked = append(parked, record.Offset)
			return nil
		})

	if err != nil || !slices.Equal(parked, []int64{0}) || records != 1 || rows != 2 || written != 10 {
		t.Fatalf("err %v, parked %v (%d records, %d rows), written %d; want nil, [0], 1, 2, 10",
			err, parked, records, rows, written)
	}
}
