package consume

import (
	"context"
	"errors"
	"testing"

	"github.com/twmb/franz-go/pkg/kgo"
)

type recordingParker struct {
	parked []*kgo.Record
	fail   error
}

func (r *recordingParker) Park(_ context.Context, record *kgo.Record, _ error) error {
	if r.fail != nil {
		return r.fail
	}
	r.parked = append(r.parked, record)
	return nil
}

func TestEveryRecordInARefusedChunkIsParked(t *testing.T) {
	chunk := []*kgo.Record{{Offset: 1}, {Offset: 2}, {Offset: 3}}
	parker := &recordingParker{}

	parked, err := ParkRefused(context.Background(), parker, chunk, AsPermanent(errors.New("refused")))

	if err != nil || parked != 3 || len(parker.parked) != 3 {
		t.Fatalf("parked %d (%d recorded), err %v; want all 3 and no error", parked, len(parker.parked), err)
	}
}

// A park that fails must stop the offset from moving, so the failure is returned
// rather than counted as parked.
func TestAFailedParkIsReturned(t *testing.T) {
	broker := errors.New("not leader for partition")
	_, err := ParkRefused(context.Background(), &recordingParker{fail: broker},
		[]*kgo.Record{{Offset: 1}}, AsPermanent(errors.New("refused")))
	if !errors.Is(err, broker) {
		t.Fatalf("err = %v, want the broker's failure", err)
	}
}
