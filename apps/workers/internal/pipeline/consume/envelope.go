package consume

import (
	"fmt"
	"time"

	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// Rejection is a message a pipeline cannot process, carrying the coordinates
// established before it was rejected so the parked message can be attributed to
// a customer. A parked message failed to decode and so also bypassed masking:
// an unmasked payload nobody can trace is one nobody can disclose or delete.
//
// The fields come from the envelope the receiver authenticated, never from the
// payload, and only once that envelope decoded cleanly — protobuf leaves a
// message half-populated when it fails, and half-parsed bytes are not an
// identity.
type Rejection struct {
	Cause     error
	IngestID  string
	TenantID  string
	ProjectID string
}

func (r *Rejection) Error() string { return r.Cause.Error() }

func (r *Rejection) Unwrap() error { return r.Cause }

// RejectedBy names the envelope a rejected message belonged to. Every stage
// that fails after the envelope decoded should reject through it, so the caller
// never has to ask whether it knows who the message belonged to.
func RejectedBy(envelope *nexovuev1.Envelope, cause error) *Rejection {
	return &Rejection{
		Cause:     cause,
		IngestID:  envelope.GetIngestId(),
		TenantID:  envelope.GetTenantId(),
		ProjectID: envelope.GetProjectId(),
	}
}

// DecodeEnvelope unpacks the internal envelope every door normalizes to, and
// refuses one carrying a signal this pipeline does not consume — a topic
// holding another signal's messages is a misconfiguration, not a payload to
// guess at. Every failure is a *Rejection.
//
// Decoding stops at the envelope rather than continuing into the payload so
// that the mask stage can run on the decoded payload. Masking the payload
// rather than the rows is what guarantees an unmasked value cannot reach a
// counter or a sampled write — the stages after it never see the original.
func DecodeEnvelope(message []byte, want nexovuev1.Signal) (*nexovuev1.Envelope, error) {
	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(message, &envelope); err != nil {
		// The bytes are not our envelope at all, so there is nothing to
		// attribute them to.
		return nil, &Rejection{Cause: fmt.Errorf("decode envelope: %w", err)}
	}
	if envelope.GetSignal() != want {
		return nil, RejectedBy(&envelope, fmt.Errorf(
			"envelope carries signal %s, want %s", envelope.GetSignal(), want))
	}
	return &envelope, nil
}

// ReceivedAt is when the receiver took this message. It is the reference clock
// the counters judge a client's timestamp against, so it must never be zero: a
// message from before the field was populated falls back to now, which is the
// closest reading of our own clock available.
func ReceivedAt(envelope *nexovuev1.Envelope, now time.Time) time.Time {
	nanos := envelope.GetReceivedAtUnixNano()
	if nanos <= 0 {
		return now
	}
	return time.Unix(0, nanos).UTC()
}
