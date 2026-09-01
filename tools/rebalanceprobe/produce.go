package main

import (
	"context"
	"fmt"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
	"github.com/twmb/franz-go/pkg/kgo"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
)

// semconvVersion is what the payloads speak. Fixed rather than empty so the
// worker's normalisation takes the same branch it takes for real traffic.
const semconvVersion = "1.27.0"

// tally is what the broker said about one signal's messages, counted in rows as
// well as messages because one message carries many of them.
type tally struct {
	messagesAccepted atomic.Int64
	messagesFailed   atomic.Int64
	rowsAccepted     atomic.Int64
	rowsPerMessage   int
}

func (t *tally) accepted() { t.messagesAccepted.Add(1); t.rowsAccepted.Add(int64(t.rowsPerMessage)) }

func (t *tally) failed() { t.messagesFailed.Add(1) }

// envelopeFor wraps an OTLP payload in the frozen internal envelope — the same
// message the receiver writes, so the worker sees no difference.
func envelopeFor(tenant, project string, signal nexovuev1.Signal, payload proto.Message) ([]byte, error) {
	encoded, err := proto.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("encoding %s payload: %w", signal, err)
	}

	ingestID, err := uuid.NewV7()
	if err != nil {
		return nil, fmt.Errorf("generating ingest id: %w", err)
	}

	message, err := proto.Marshal(&nexovuev1.Envelope{
		TenantId:           tenant,
		ProjectId:          project,
		Door:               nexovuev1.Door_DOOR_OTLP,
		Signal:             signal,
		ReceivedAtUnixNano: time.Now().UnixNano(),
		IngestId:           ingestID.String(),
		SemconvVersion:     semconvVersion,
		Payload:            encoded,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding envelope for %s: %w", signal, err)
	}
	return message, nil
}

// producer holds the broker connection every signal loop shares.
type producer struct {
	client *kgo.Client

	// publish is how long a buffered record may wait — the process's own
	// lifetime, not the run's. Cancelling it aborts records still in the
	// client's buffer, and a record aborted at the run's deadline may well have
	// reached the broker: counting it as failed would understate what was
	// accepted and read back as a loss.
	publish context.Context
}

// dial opens a producer that addresses partitions itself. The receiver cannot:
// it keys log and metric records by tenant, so one tenant's records all hash to
// one partition and a rebalance could only ever hand that partition to one
// worker. Choosing the partition here is what puts a signal on every one of
// them.
func dial(publish context.Context, brokers []string) (*producer, error) {
	client, err := kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.RecordPartitioner(kgo.ManualPartitioner()),
		kgo.ProducerBatchCompression(kgo.ZstdCompression()),
	)
	if err != nil {
		return nil, fmt.Errorf("kafka producer: %w", err)
	}
	return &producer{client: client, publish: publish}, nil
}

func (p *producer) close() { p.client.Close() }

// send publishes one message and records the broker's answer. A record is
// counted as accepted only when the broker acknowledged it, so the stored count
// is measured against what was actually taken.
func (p *producer) send(topic string, partition int32, value []byte, into *tally) {
	record := &kgo.Record{Topic: topic, Partition: partition, Value: value}
	p.client.Produce(p.publish, record, func(_ *kgo.Record, err error) {
		if err != nil {
			into.failed()
			return
		}
		into.accepted()
	})
}

// flush waits for every buffered record to be acknowledged or fail, so the
// report is written against a settled tally.
func (p *producer) flush(ctx context.Context) error {
	if err := p.client.Flush(ctx); err != nil {
		return fmt.Errorf("flushing the producer: %w", err)
	}
	return nil
}
