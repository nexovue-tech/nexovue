package traceassembly

import (
	"errors"
	"testing"

	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
)

// unreadable is not valid protobuf at any level: the first tag never
// terminates.
var unreadable = []byte{0xff, 0xfe}

// A parked message skipped the mask stage by construction — it is parked
// because it failed to decode, and masking runs on a decoded payload. So the
// dead-letter topic holds unmasked customer data, and an exposure that cannot
// be traced to a customer can neither be disclosed to them nor deleted on
// request. The envelope names them, and a span payload that fails to decode
// after the envelope opened has to carry that forward.
func TestAnUnreadableSpanPayloadIsRejectedWithItsTenant(t *testing.T) {
	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:  "tenant-a",
		ProjectId: "project-a",
		IngestId:  "ingest-a",
		Door:      nexovuev1.Door_DOOR_OTLP,
		Signal:    nexovuev1.Signal_SIGNAL_SPANS,
		Payload:   unreadable,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}

	if _, err := decodeEnvelope(message); err == nil {
		t.Fatal("decodeEnvelope() succeeded; this message must be parked")
	} else if !attributed(t, err) {
		t.Error("the rejection named no tenant, so the parked message cannot be attributed")
	}
}

func attributed(t *testing.T, err error) bool {
	t.Helper()
	var rejection *consume.Rejection
	if !errors.As(err, &rejection) {
		t.Fatalf("decodeEnvelope() returned %T, want a *consume.Rejection", err)
	}
	return rejection.TenantID == "tenant-a" &&
		rejection.ProjectID == "project-a" &&
		rejection.IngestID == "ingest-a"
}
