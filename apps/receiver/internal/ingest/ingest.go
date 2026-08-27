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
}

// NewPublisher returns a publisher writing spans to spansTopic.
func NewPublisher(producer Producer, spansTopic string) *Publisher {
	return &Publisher{producer: producer, spansTopic: spansTopic}
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

	if err := p.producer.ProduceSync(ctx, records...).FirstErr(); err != nil {
		return fmt.Errorf("producing to %s: %w", p.spansTopic, err)
	}
	return nil
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
