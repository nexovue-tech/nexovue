package consume

import (
	"errors"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
)

// unreadable is not valid protobuf at any level: the first tag never
// terminates.
var unreadable = []byte{0xff, 0xfe}

func envelopeCarrying(t *testing.T, signal nexovuev1.Signal) []byte {
	t.Helper()
	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:  "tenant-a",
		ProjectId: "project-a",
		IngestId:  "ingest-a",
		Door:      nexovuev1.Door_DOOR_OTLP,
		Signal:    signal,
		Payload:   unreadable,
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
	rejections := map[string]error{
		"the envelope is on the wrong topic": rejectionOf(
			t, envelopeCarrying(t, nexovuev1.Signal_SIGNAL_LOGS)),
		// What a signal's own decoder returns once the envelope has opened and
		// its payload turns out to be unreadable.
		"the payload is unreadable": RejectedBy(&nexovuev1.Envelope{
			TenantId: "tenant-a", ProjectId: "project-a", IngestId: "ingest-a",
		}, errors.New("decode payload")),
	}

	for name, rejection := range rejections {
		t.Run(name, func(t *testing.T) {
			details := attribution(rejection)
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

// A topic holding another signal's messages is a misconfiguration, and decoding
// its payload as ours would either fail obscurely or, worse, succeed.
func TestAnEnvelopeCarryingAnotherSignalIsRejected(t *testing.T) {
	if _, err := DecodeEnvelope(
		envelopeCarrying(t, nexovuev1.Signal_SIGNAL_LOGS),
		nexovuev1.Signal_SIGNAL_SPANS,
	); err == nil {
		t.Fatal("DecodeEnvelope() accepted an envelope carrying the wrong signal")
	}
}

// When the bytes are not our envelope at all there is no authenticated tenant
// to name. Protobuf leaves a message half-populated when it fails, and
// attributing an exposure to whatever survived a failed parse would be worse
// than admitting we do not know.
func TestAMessageThatIsNotAnEnvelopeIsParkedUnattributed(t *testing.T) {
	err := rejectionOf(t, unreadable)

	if details := attribution(err); details != (dlq.Context{}) {
		t.Errorf("attribution() = %+v, want nothing claimed", details)
	}
}

// The counters bucket on the receiver's clock, so it must never be zero: a
// message from before the field was populated is judged at the moment we read
// it rather than at the epoch.
func TestAnEnvelopeWithNoReceiveTimeFallsBackToNow(t *testing.T) {
	now := time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)
	stamped := now.Add(-time.Minute)

	if got := ReceivedAt(&nexovuev1.Envelope{}, now); !got.Equal(now) {
		t.Errorf("ReceivedAt() = %s for an unstamped envelope, want %s", got, now)
	}
	got := ReceivedAt(&nexovuev1.Envelope{ReceivedAtUnixNano: stamped.UnixNano()}, now)
	if !got.Equal(stamped) {
		t.Errorf("ReceivedAt() = %s, want the receiver's own stamp %s", got, stamped)
	}
}

func rejectionOf(t *testing.T, message []byte) error {
	t.Helper()
	_, err := DecodeEnvelope(message, nexovuev1.Signal_SIGNAL_SPANS)
	if err == nil {
		t.Fatal("DecodeEnvelope() succeeded; this message must be parked")
	}
	return err
}
