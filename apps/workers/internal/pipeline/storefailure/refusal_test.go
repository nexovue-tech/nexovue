package storefailure

import (
	"errors"
	"fmt"
	"testing"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

func exception(code proto.Error) error {
	return fmt.Errorf("insert 1 spans: %w", &ch.Exception{Code: code, Name: code.String()})
}

func TestARefusalOfTheDataItselfIsPermanent(t *testing.T) {
	for _, code := range []proto.Error{
		proto.ErrIncorrectData, // what a 1,100-level attribute key gets (ingest-cannot-be-knocked-over)
		proto.ErrTypeMismatch,
		proto.ErrIllegalColumn,
		proto.ErrCannotParseText,
	} {
		t.Run(code.String(), func(t *testing.T) {
			if !RefusedTheData(exception(code)) {
				t.Errorf("RefusedTheData(%s) = false, want true", code)
			}
			if !consume.IsPermanent(Judged(exception(code))) {
				t.Errorf("Judged(%s) is not permanent: the loop would retry it until the worker exits", code)
			}
		})
	}
}

// An outage is weather. Judging one permanent parks records that would have
// been written a second later.
func TestAnOutageIsNotPermanent(t *testing.T) {
	for name, err := range map[string]error{
		"server busy":     exception(proto.ErrTooManySimultaneousQueries),
		"dropped socket":  errors.New("packet: uvarint: read: read: EOF"),
		"refused connect": errors.New("dial tcp 172.18.0.4:9000: connect: connection refused"),
	} {
		t.Run(name, func(t *testing.T) {
			if consume.IsPermanent(Judged(err)) {
				t.Errorf("Judged(%v) is permanent; an outage must be retried", err)
			}
		})
	}
	if Judged(nil) != nil {
		t.Error("Judged(nil) must stay nil")
	}
}
