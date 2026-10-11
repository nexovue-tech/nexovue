// Package ingest wraps a normalized payload in the frozen envelope and hands it
// to the broker.
//
// This is the last gate. Downstream sees one payload format whatever door the
// data came from; the envelope's door names the entrance, and the usage meter
// bills by it. The HTTP request is not acknowledged until the broker
// has the message — the receiver never accepts data it has not managed to hand
// on.
package ingest

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/twmb/franz-go/pkg/kgo"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/tracesplit"
)

// Producer is the broker as this package uses it. An interface so the envelope
// construction is testable without a Kafka cluster.
type Producer interface {
	ProduceSync(ctx context.Context, records ...*kgo.Record) kgo.ProduceResults
}

// DoorFor is the entrance an identity's telemetry is stamped as having come
// through, and it is decided by the kind of key that opened it: a public key is
// printed in a web page and can only open the browser door, so its
// bytes are the browser door's. The usage meter bills the browser door to its
// own budget, and the allowance gate asks about the same door this
// stamps, so the two cannot disagree about which budget an export spends.
//
// Written as an allow-list because auth.Kind has no safe zero: a kind nobody
// set is an authentication defect, not an entrance, and it names no door. Both
// readers of the field then charge it to the server's share — the browser's is
// the small one anyone can spend, and traffic we cannot place must not be
// handed it.
func DoorFor(identity auth.Identity) nexovuev1.Door {
	switch identity.Kind {
	case auth.KindSecret:
		return nexovuev1.Door_DOOR_OTLP
	case auth.KindPublic:
		return nexovuev1.Door_DOOR_RUM
	default:
		return nexovuev1.Door_DOOR_UNSPECIFIED
	}
}

// Topics names where each signal's envelopes are published. One topic per
// signal, so a consumer subscribes to the shape it knows how to read.
type Topics struct {
	Spans   string
	Logs    string
	Metrics string
	// Profiles carries nexovue.v1.ProfileSamples, not OTLP (ADR-015).
	Profiles string
}

// Publisher publishes envelopes to the ingestion topics.
type Publisher struct {
	producer Producer
	topics   Topics
	budget   time.Duration
}

// NewPublisher returns a publisher writing to topics, spending at most budget on
// any one publish before it reports failure.
func NewPublisher(producer Producer, topics Topics, budget time.Duration) *Publisher {
	return &Publisher{producer: producer, topics: topics, budget: budget}
}

// Batch is one export forwarded exactly as it arrived: the signal it carries,
// the OTLP request itself, and the convention generation it declares.
//
// Only traces are split before publishing, because trace assembly depends on
// Kafka partitioning by trace id. Logs and metrics have no such requirement, so
// they travel as one envelope per export.
type Batch struct {
	Signal         nexovuev1.Signal
	SemconvVersion string
	Request        proto.Message
}

// PublishTraces sends one envelope per trace, keyed by trace id so that every
// span of a trace lands on the partition the assembler will read it from.
//
// It returns only once the broker has acknowledged every message.
func (p *Publisher) PublishTraces(
	ctx context.Context,
	identity auth.Identity,
	receivedAt time.Time,
	groups []tracesplit.Group,
) error {
	records := make([]*kgo.Record, 0, len(groups))
	for _, group := range groups {
		record, err := p.spanRecord(identity, receivedAt, group)
		if err != nil {
			return err
		}
		records = append(records, record)
	}
	return p.publish(ctx, p.topics.Spans, records)
}

// PublishBatch sends one export on whole, keyed by tenant id: nothing downstream
// of a log or metric batch reassembles it, so the only thing partitioning owes
// is that one tenant's data stays together.
//
// It returns only once the broker has acknowledged the message.
func (p *Publisher) PublishBatch(
	ctx context.Context,
	identity auth.Identity,
	receivedAt time.Time,
	batch Batch,
) error {
	return p.PublishBatches(ctx, identity, receivedAt, []Batch{batch})
}

// PublishBatches publishes several envelopes as one produce: one wait for the
// broker, and the whole set acknowledged or refused together as far as one produce
// can make it — a request the receiver split must not be answered part by part.
func (p *Publisher) PublishBatches(
	ctx context.Context,
	identity auth.Identity,
	receivedAt time.Time,
	batches []Batch,
) error {
	if len(batches) == 0 {
		return nil
	}
	topic, err := p.topicFor(batches[0].Signal)
	if err != nil {
		return err
	}
	records := make([]*kgo.Record, 0, len(batches))
	for _, batch := range batches {
		if batch.Signal != batches[0].Signal {
			return fmt.Errorf("one publish carries one signal, got %v and %v", batches[0].Signal, batch.Signal)
		}
		record, err := p.batchRecord(topic, identity, receivedAt, batch)
		if err != nil {
			return err
		}
		records = append(records, record)
	}
	return p.publish(ctx, topic, records)
}

func (p *Publisher) topicFor(signal nexovuev1.Signal) (string, error) {
	switch signal {
	case nexovuev1.Signal_SIGNAL_SPANS:
		return p.topics.Spans, nil
	case nexovuev1.Signal_SIGNAL_LOGS:
		return p.topics.Logs, nil
	case nexovuev1.Signal_SIGNAL_METRICS:
		return p.topics.Metrics, nil
	case nexovuev1.Signal_SIGNAL_PROFILES:
		return p.topics.Profiles, nil
	case nexovuev1.Signal_SIGNAL_UNSPECIFIED:
		fallthrough
	default:
		return "", fmt.Errorf("no ingestion topic for signal %s", signal)
	}
}

// publish hands records to the broker and returns only once it holds every one
// of them, or the attempt is out of budget.
func (p *Publisher) publish(ctx context.Context, topic string, records []*kgo.Record) error {
	if len(records) == 0 {
		return nil
	}

	// One budget for the whole publish. franz-go blocks a produce once its buffer
	// is full and only a context deadline breaks that wait, so without this a
	// handler goroutine waits out a broker outage holding the request's memory.
	ctx, cancel := context.WithTimeout(ctx, p.budget)
	defer cancel()

	// Retry only what did not land. ProduceSync reports the first error while
	// other records in the same request may already be on the broker, so
	// answering the client "failed" makes it retry the whole export and publish
	// those again — the receiver breaking its own ack contract in the direction
	// nobody looks at. Retrying the failed subset means the answer is true:
	// either everything is published, or nothing new was.
	remaining := records
	for attempt := 1; ; attempt++ {
		failed := unpublished(p.producer.ProduceSync(ctx, remaining...))
		if len(failed) == 0 {
			return nil
		}
		if len(failed) == len(remaining) || attempt >= produceAttempts || ctx.Err() != nil {
			return fmt.Errorf("producing %d of %d records to %s: %w",
				len(failed), len(records), topic, failed[0].err)
		}
		remaining = make([]*kgo.Record, 0, len(failed))
		for _, outcome := range failed {
			remaining = append(remaining, outcome.record)
		}
	}
}

// produceAttempts guards against spinning on a partial failure that repeats. It
// is not the durability bound: franz-go retries internally within its delivery
// deadline, the customer's SDK retries underneath us, and a publish that fails
// outright is not retried here at all — three layers of retry on one path is how
// a broker outage stays an outage after the broker comes back.
const produceAttempts = 3

// outcome pairs a record with why it did not publish.
type outcome struct {
	record *kgo.Record
	err    error
}

// unpublished returns the records that did not reach the broker.
func unpublished(results kgo.ProduceResults) []outcome {
	var failed []outcome
	for _, result := range results {
		if result.Err != nil {
			failed = append(failed, outcome{record: result.Record, err: result.Err})
		}
	}
	return failed
}

// spanRecord addresses one trace's envelope by trace id, so that every span of a
// trace lands on the partition the assembler will read it from.
func (p *Publisher) spanRecord(
	identity auth.Identity,
	receivedAt time.Time,
	group tracesplit.Group,
) (*kgo.Record, error) {
	record, err := recordFor(p.topics.Spans, group.TraceID, identity, receivedAt, Batch{
		Signal:         nexovuev1.Signal_SIGNAL_SPANS,
		SemconvVersion: group.SemconvVersion,
		Request:        group.Request,
	})
	if err != nil {
		return nil, fmt.Errorf("trace %s: %w", group.TraceID, err)
	}
	return record, nil
}

func (p *Publisher) batchRecord(
	topic string,
	identity auth.Identity,
	receivedAt time.Time,
	batch Batch,
) (*kgo.Record, error) {
	// Keyed by tenant so one customer's batches stay in order relative to each
	// other; the envelope contract fixes this key.
	return recordFor(topic, identity.OrgID, identity, receivedAt, batch)
}

// recordFor wraps an OTLP payload in the frozen envelope, addressed to topic
// under key. The payload itself is never modified: the envelope carries only our
// metadata around the bytes the customer's SDK produced.
func recordFor(
	topic string,
	key string,
	identity auth.Identity,
	receivedAt time.Time,
	batch Batch,
) (*kgo.Record, error) {
	// Deterministic, so a resend of the same export marshals to the same bytes
	// and so the same digest.
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(batch.Request)
	if err != nil {
		return nil, fmt.Errorf("encoding %s payload: %w", batch.Signal, err)
	}

	ingestID, err := uuid.NewV7()
	if err != nil {
		return nil, fmt.Errorf("generating ingest id: %w", err)
	}

	envelope, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:           identity.OrgID,
		ProjectId:          identity.ProjectID,
		Door:               DoorFor(identity),
		Signal:             batch.Signal,
		ReceivedAtUnixNano: receivedAt.UnixNano(),
		IngestId:           ingestID.String(),
		SemconvVersion:     batch.SemconvVersion,
		Payload:            payload,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding envelope for %s: %w", batch.Signal, err)
	}

	return &kgo.Record{
		Topic: topic,
		Key:   []byte(key),
		Value: envelope,
		Headers: []kgo.RecordHeader{
			{Key: PayloadDigestHeader, Value: []byte(payloadDigest(identity, batch.Signal, payload))},
		},
	}, nil
}

// PayloadDigestHeader carries a digest of the record's tenant, project, signal
// and payload (group 9, C2). The same export sent again carries the same digest,
// which is how the worker recognises a sender's resend and stores it once
// (`apps/workers/internal/pipeline/resend`, which reads it under the same name).
const PayloadDigestHeader = "nexovue-payload-digest"

func payloadDigest(identity auth.Identity, signal nexovuev1.Signal, payload []byte) string {
	sum := sha256.New()
	for _, part := range []string{identity.OrgID, identity.ProjectID, signal.String()} {
		sum.Write([]byte(part))
		sum.Write([]byte{0})
	}
	sum.Write(payload)
	return hex.EncodeToString(sum.Sum(nil))
}
