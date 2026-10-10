// Package dlq parks messages the pipeline cannot process.
//
// A message that cannot be decoded must neither stall the partition behind it
// nor vanish. It is republished here with enough context to triage it — the
// reason, where it came from, and the bytes themselves — and the offset is only
// committed once the broker has acknowledged that.
//
// Nothing drains this topic automatically. A poison message that retries on a
// timer is an infinite loop with extra steps; getting one out of here is a
// deliberate act by someone who has read it.
package dlq

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

// Header names on a parked message. They are prefixed so they cannot collide
// with a header a customer's own producer might set.
const (
	HeaderReason     = "nexovue-dlq-reason"
	HeaderTopic      = "nexovue-source-topic"
	HeaderPartition  = "nexovue-source-partition"
	HeaderOffset     = "nexovue-source-offset"
	HeaderParkedAt   = "nexovue-parked-at"
	HeaderConsumer   = "nexovue-consumer-group"
	HeaderIngestID   = "nexovue-ingest-id"
	HeaderTenantID   = "nexovue-tenant-id"
	HeaderProjectID  = "nexovue-project-id"
	HeaderPayload    = "nexovue-parked-payload"
	headerTimeFormat = time.RFC3339Nano
)

// PayloadRow names a parked message whose value is one stored row as JSON — a
// row the store refused after it had already been decoded and masked — rather
// than the source message's own bytes. Absent means the source message.
const PayloadRow = "row-json"

// Producer is the broker as this package uses it. An interface so parking is
// testable without a Kafka cluster, matching how the receiver publishes.
type Producer interface {
	ProduceSync(ctx context.Context, records ...*kgo.Record) kgo.ProduceResults
}

// Context is what triage needs beyond the failure itself. Every field is
// optional: a message that failed to decode may have yielded nothing at all.
type Context struct {
	IngestID  string
	TenantID  string
	ProjectID string
	// Payload says what the parked value is when it is not the source message;
	// see PayloadRow.
	Payload string
}

// Queue parks messages on one dead-letter topic.
type Queue struct {
	producer      Producer
	topic         string
	consumerGroup string
	now           func() time.Time // replaced in tests
}

// New returns a queue publishing to topic. consumerGroup is recorded on every
// parked message, because "which consumer could not read this" is the first
// question asked of one.
func New(producer Producer, topic, consumerGroup string) *Queue {
	return &Queue{producer: producer, topic: topic, consumerGroup: consumerGroup, now: time.Now}
}

// Park republishes a message that could not be processed, keeping its key so a
// trace's poison message stays with the rest of that trace's coordinates.
//
// It returns only once the broker has acknowledged. The caller must not commit
// the source offset before that: a park that failed and an offset that moved on
// is exactly the data loss the dead-letter topic exists to prevent.
func (q *Queue) Park(
	ctx context.Context,
	record *kgo.Record,
	reason string,
	details Context,
) error {
	parked := &kgo.Record{
		Topic:   q.topic,
		Key:     record.Key,
		Value:   record.Value,
		Headers: q.headers(record, reason, details),
	}
	if err := q.producer.ProduceSync(ctx, parked).FirstErr(); err != nil {
		return fmt.Errorf("parking %s/%d@%d in %s: %w",
			record.Topic, record.Partition, record.Offset, q.topic, err)
	}
	return nil
}

func (q *Queue) headers(record *kgo.Record, reason string, details Context) []kgo.RecordHeader {
	headers := []kgo.RecordHeader{
		{Key: HeaderReason, Value: []byte(reason)},
		{Key: HeaderTopic, Value: []byte(record.Topic)},
		{Key: HeaderPartition, Value: []byte(strconv.FormatInt(int64(record.Partition), 10))},
		{Key: HeaderOffset, Value: []byte(strconv.FormatInt(record.Offset, 10))},
		{Key: HeaderParkedAt, Value: []byte(q.now().UTC().Format(headerTimeFormat))},
		{Key: HeaderConsumer, Value: []byte(q.consumerGroup)},
	}
	headers = appendIfSet(headers, HeaderIngestID, details.IngestID)
	headers = appendIfSet(headers, HeaderTenantID, details.TenantID)
	headers = appendIfSet(headers, HeaderProjectID, details.ProjectID)
	return appendIfSet(headers, HeaderPayload, details.Payload)
}

func appendIfSet(headers []kgo.RecordHeader, key, value string) []kgo.RecordHeader {
	if value == "" {
		return headers
	}
	return append(headers, kgo.RecordHeader{Key: key, Value: []byte(value)})
}
