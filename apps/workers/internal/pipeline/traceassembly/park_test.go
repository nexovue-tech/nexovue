package traceassembly

import (
	"testing"

	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
)

// unreadable is not valid protobuf at any level: the first tag never
// terminates.
var unreadable = []byte{0xff, 0xfe}

func envelopeCarrying(t *testing.T, signal nexovuev1.Signal, payload []byte) []byte {
	t.Helper()
	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:  "tenant-a",
		ProjectId: "project-a",
		IngestId:  "ingest-a",
		Door:      nexovuev1.Door_DOOR_OTLP,
		Signal:    signal,
		Payload:   payload,
	})
	if err != nil {
		t.Fatalf("marshalling the envelope: %v", err)
	}
	return message
}

// A parked message skipped the mask stage by construction — it is parked
// because it failed to decode, and masking runs on a decoded payload. So the
// dead-letter topic holds unmasked customer data, and an exposure that cannot
// be traced to a customer can neither be disclosed to them nor deleted on
// request. The envelope names them; parking has to carry that.
func TestAParkedMessageIsAttributedToItsTenant(t *testing.T) {
	cases := map[string][]byte{
		"the OTLP payload is unreadable": envelopeCarrying(
			t, nexovuev1.Signal_SIGNAL_SPANS, unreadable),
		"the envelope is on the wrong topic": envelopeCarrying(
			t, nexovuev1.Signal_SIGNAL_LOGS, unreadable),
	}

	for name, message := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := decodeEnvelope(message)
			if err == nil {
				t.Fatal("decodeEnvelope() succeeded; this message must be parked")
			}

			details := parkContext(err)
			if details.TenantID != "tenant-a" {
				t.Errorf("TenantID = %q, want the tenant the envelope named", details.TenantID)
			}
			if details.ProjectID != "project-a" {
				t.Errorf("ProjectID = %q, want the project the envelope named", details.ProjectID)
			}
			if details.IngestID != "ingest-a" {
				t.Errorf("IngestID = %q, want the receiver's ingest id", details.IngestID)
			}
		})
	}
}

// When the bytes are not our envelope at all there is no authenticated tenant
// to name. Protobuf leaves a message half-populated when it fails, and
// attributing an exposure to whatever survived a failed parse would be worse
// than admitting we do not know.
func TestAMessageThatIsNotAnEnvelopeIsParkedUnattributed(t *testing.T) {
	_, err := decodeEnvelope(unreadable)
	if err == nil {
		t.Fatal("decodeEnvelope() succeeded on bytes that are not protobuf")
	}
	if details := parkContext(err); details != (dlq.Context{}) {
		t.Errorf("parkContext() = %+v, want nothing claimed", details)
	}
}
