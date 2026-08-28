// Package ingest wraps a normalized payload in the frozen envelope and hands it
// to the broker.
//
// This is the last gate. Nothing downstream of it knows which door the data
// came from, and the HTTP request is not acknowledged until the broker has the
// message — the receiver never accepts data it has not managed to hand on.
package ingest

import (
	"context"
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

// Publisher publishes envelopes to the ingestion topics.
type Publisher struct {
	producer   Producer
	spansTopic string
	budget     time.Duration
}

// NewPublisher returns a publisher writing spans to spansTopic, spending at most
// budget on any one publish before it reports failure.
func NewPublisher(producer Producer, spansTopic string, budget time.Duration) *Publisher {
	return &Publisher{producer: producer, spansTopic: spansTopic, budget: budget}
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
	if len(groups) == 0 {
		return nil
	}

	records := make([]*kgo.Record, 0, len(groups))
	for _, group := range groups {
		record, err := p.spanRecord(identity, receivedAt, group)
		if err != nil {
			return err
		}
		records = append(records, record)
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
				len(failed), len(records), p.spansTopic, failed[0].err)
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

func (p *Publisher) spanRecord(
	identity auth.Identity,
	receivedAt time.Time,
	group tracesplit.Group,
) (*kgo.Record, error) {
	payload, err := proto.Marshal(group.Request)
	if err != nil {
		return nil, fmt.Errorf("encoding trace %s: %w", group.TraceID, err)
	}

	ingestID, err := uuid.NewV7()
	if err != nil {
		return nil, fmt.Errorf("generating ingest id: %w", err)
	}

	envelope, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:           identity.OrgID,
		ProjectId:          identity.ProjectID,
		Door:               nexovuev1.Door_DOOR_OTLP,
		Signal:             nexovuev1.Signal_SIGNAL_SPANS,
		ReceivedAtUnixNano: receivedAt.UnixNano(),
		IngestId:           ingestID.String(),
		SemconvVersion:     group.SemconvVersion,
		Payload:            payload,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding envelope for trace %s: %w", group.TraceID, err)
	}

	return &kgo.Record{
		Topic: p.spansTopic,
		Key:   []byte(group.TraceID),
		Value: envelope,
	}, nil
}
