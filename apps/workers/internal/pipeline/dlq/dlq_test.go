package dlq_test

import (
	"context"
	"errors"
	"testing"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
)

// fakeProducer records what was published, and can be told to fail.
type fakeProducer struct {
	published []*kgo.Record
	err       error
}

func (f *fakeProducer) ProduceSync(_ context.Context, records ...*kgo.Record) kgo.ProduceResults {
	if f.err != nil {
		return kgo.ProduceResults{{Err: f.err}}
	}
	f.published = append(f.published, records...)
	results := make(kgo.ProduceResults, 0, len(records))
	for _, record := range records {
		results = append(results, kgo.ProduceResult{Record: record})
	}
	return results
}

func poisonRecord() *kgo.Record {
	return &kgo.Record{
		Topic:     "spans-raw",
		Partition: 7,
		Offset:    4242,
		Key:       []byte("trace-a"),
		Value:     []byte{0xff, 0xfe},
	}
}

func headerValue(record *kgo.Record, key string) string {
	for _, header := range record.Headers {
		if header.Key == key {
			return string(header.Value)
		}
	}
	return ""
}

// A parked message with no coordinates cannot be triaged, which makes the
// dead-letter topic a slower way of losing it.
func TestAParkedMessageCarriesEnoughToTriageIt(t *testing.T) {
	producer := &fakeProducer{}
	queue := dlq.New(producer, "spans-dlq", "nexovue-workers")

	source := poisonRecord()
	err := queue.Park(context.Background(), source, "decode envelope: bad wire type",
		dlq.Context{IngestID: "ingest-1", TenantID: "tenant-1", ProjectID: "project-1"})
	if err != nil {
		t.Fatalf("Park() = %v, want no error", err)
	}
	if len(producer.published) != 1 {
		t.Fatalf("published %d records, want 1", len(producer.published))
	}
	parked := producer.published[0]

	if parked.Topic != "spans-dlq" {
		t.Errorf("Topic = %q, want spans-dlq", parked.Topic)
	}
	if string(parked.Value) != string(source.Value) {
		t.Errorf("Value = %v, want the original bytes %v", parked.Value, source.Value)
	}
	if string(parked.Key) != string(source.Key) {
		t.Errorf("Key = %q, want the original key %q", parked.Key, source.Key)
	}

	want := map[string]string{
		dlq.HeaderReason:    "decode envelope: bad wire type",
		dlq.HeaderTopic:     "spans-raw",
		dlq.HeaderPartition: "7",
		dlq.HeaderOffset:    "4242",
		dlq.HeaderConsumer:  "nexovue-workers",
		dlq.HeaderIngestID:  "ingest-1",
		dlq.HeaderTenantID:  "tenant-1",
		dlq.HeaderProjectID: "project-1",
	}
	for key, wanted := range want {
		if got := headerValue(parked, key); got != wanted {
			t.Errorf("header %s = %q, want %q", key, got, wanted)
		}
	}
	if headerValue(parked, dlq.HeaderParkedAt) == "" {
		t.Errorf("header %s is empty, want the time it was parked", dlq.HeaderParkedAt)
	}
}

// Context fields are absent when the message decoded to nothing at all, and an
// empty header is worse than no header.
func TestUnknownContextIsOmittedRatherThanWrittenEmpty(t *testing.T) {
	producer := &fakeProducer{}
	queue := dlq.New(producer, "spans-dlq", "nexovue-workers")

	if err := queue.Park(context.Background(), poisonRecord(), "unreadable", dlq.Context{}); err != nil {
		t.Fatalf("Park() = %v, want no error", err)
	}
	for _, key := range []string{dlq.HeaderIngestID, dlq.HeaderTenantID, dlq.HeaderProjectID} {
		if headerValue(producer.published[0], key) != "" {
			t.Errorf("header %s was written for an unknown value", key)
		}
	}
}

// A park that failed and an offset that moved on is exactly the loss the
// dead-letter topic exists to prevent, so the failure has to reach the caller.
func TestAFailedParkIsReported(t *testing.T) {
	producer := &fakeProducer{err: errors.New("broker unavailable")}
	queue := dlq.New(producer, "spans-dlq", "nexovue-workers")

	err := queue.Park(context.Background(), poisonRecord(), "unreadable", dlq.Context{})
	if err == nil {
		t.Fatal("Park() succeeded while the broker refused it")
	}
	if !errors.Is(err, producer.err) {
		t.Errorf("Park() = %v, want it to wrap the broker's error", err)
	}
}
