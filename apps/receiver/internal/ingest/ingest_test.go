package ingest_test

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	collectorpb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"

	"github.com/nexovue-tech/nexovue/apps/receiver/internal/auth"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/ingest"
	"github.com/nexovue-tech/nexovue/apps/receiver/internal/tracesplit"
)

const (
	spansTopic = "spans-raw"

	// testBudget is long enough that a working producer never trips it, short
	// enough that a test proving the bound does not stall the suite.
	testBudget = 200 * time.Millisecond
	orgID      = "01996d5e-8f00-7a11-9c3e-4b2f6a8d1e00"
	projectID  = "01996d5e-9a40-7b22-8d4f-5c3a7b9e2f11"
)

// fakeProducer records what would have been published.
type fakeProducer struct {
	records []*kgo.Record
	err     error
}

func (f *fakeProducer) ProduceSync(_ context.Context, records ...*kgo.Record) kgo.ProduceResults {
	if f.err != nil {
		return kgo.ProduceResults{{Err: f.err}}
	}
	f.records = append(f.records, records...)
	results := make(kgo.ProduceResults, 0, len(records))
	for _, record := range records {
		results = append(results, kgo.ProduceResult{Record: record})
	}
	return results
}

func identity() auth.Identity {
	return auth.Identity{OrgID: orgID, ProjectID: projectID, KeyPrefix: "nvk_live_dev"}
}

func groupFor(traceID string, semconv string) tracesplit.Group {
	return tracesplit.Group{
		TraceID:        traceID,
		SemconvVersion: semconv,
		Request: &collectorpb.ExportTraceServiceRequest{
			ResourceSpans: []*tracepb.ResourceSpans{{
				ScopeSpans: []*tracepb.ScopeSpans{{Spans: []*tracepb.Span{{Name: "handler"}}}},
			}},
		},
	}
}

func TestPublishTracesWrapsEachTraceInAnEnvelope(t *testing.T) {
	producer := &fakeProducer{}
	receivedAt := time.Unix(1787649300, 430_000_000)

	err := ingest.NewPublisher(producer, spansTopic, testBudget).PublishTraces(
		context.Background(), identity(), receivedAt, []tracesplit.Group{groupFor("aabb", "1.43.0")},
	)
	if err != nil {
		t.Fatalf("PublishTraces() = %v, want no error", err)
	}
	if len(producer.records) != 1 {
		t.Fatalf("produced %d records, want 1", len(producer.records))
	}

	record := producer.records[0]
	if record.Topic != spansTopic {
		t.Errorf("topic = %q, want the configured %q", record.Topic, spansTopic)
	}
	if string(record.Key) != "aabb" {
		t.Errorf("key = %q, want the trace id: partitioning is what keeps a trace together",
			record.Key)
	}

	var envelope nexovuev1.Envelope
	if err := proto.Unmarshal(record.Value, &envelope); err != nil {
		t.Fatalf("record value is not an Envelope: %v", err)
	}
	if envelope.GetTenantId() != orgID {
		t.Errorf("tenant_id = %q, want the org id %q", envelope.GetTenantId(), orgID)
	}
	if envelope.GetProjectId() != projectID {
		t.Errorf("project_id = %q, want %q", envelope.GetProjectId(), projectID)
	}
	if envelope.GetDoor() != nexovuev1.Door_DOOR_OTLP {
		t.Errorf("door = %v, want DOOR_OTLP", envelope.GetDoor())
	}
	if envelope.GetSignal() != nexovuev1.Signal_SIGNAL_SPANS {
		t.Errorf("signal = %v, want SIGNAL_SPANS", envelope.GetSignal())
	}
	if envelope.GetReceivedAtUnixNano() != receivedAt.UnixNano() {
		t.Errorf("received_at_unix_nano = %d, want %d",
			envelope.GetReceivedAtUnixNano(), receivedAt.UnixNano())
	}
	if envelope.GetSemconvVersion() != "1.43.0" {
		t.Errorf("semconv_version = %q, want the version the payload declared",
			envelope.GetSemconvVersion())
	}
	if envelope.GetIngestId() == "" {
		t.Error("ingest_id is empty; DLQ correlation depends on it")
	}

	var payload collectorpb.ExportTraceServiceRequest
	if err := proto.Unmarshal(envelope.GetPayload(), &payload); err != nil {
		t.Fatalf("payload is not an ExportTraceServiceRequest: %v", err)
	}
	if len(payload.GetResourceSpans()) != 1 {
		t.Errorf("payload carries %d resources, want the split request unmodified",
			len(payload.GetResourceSpans()))
	}
}

func TestPublishTracesGivesEveryEnvelopeItsOwnIngestID(t *testing.T) {
	producer := &fakeProducer{}

	err := ingest.NewPublisher(producer, spansTopic, testBudget).PublishTraces(
		context.Background(), identity(), time.Now(),
		[]tracesplit.Group{groupFor("aa", ""), groupFor("bb", "")},
	)
	if err != nil {
		t.Fatalf("PublishTraces() = %v, want no error", err)
	}

	seen := map[string]bool{}
	for _, record := range producer.records {
		var envelope nexovuev1.Envelope
		if err := proto.Unmarshal(record.Value, &envelope); err != nil {
			t.Fatalf("record value is not an Envelope: %v", err)
		}
		if seen[envelope.GetIngestId()] {
			t.Fatalf("ingest_id %q was reused", envelope.GetIngestId())
		}
		seen[envelope.GetIngestId()] = true
	}
}

func TestPublishTracesDoesNothingForAnEmptyBatch(t *testing.T) {
	producer := &fakeProducer{}

	err := ingest.NewPublisher(producer, spansTopic, testBudget).PublishTraces(
		context.Background(), identity(), time.Now(), nil,
	)
	if err != nil {
		t.Fatalf("PublishTraces() = %v, want no error", err)
	}
	if len(producer.records) != 0 {
		t.Errorf("produced %d records for a batch with no traces", len(producer.records))
	}
}

func TestPublishTracesReportsABrokerRefusal(t *testing.T) {
	refused := errors.New("kafka: not leader for partition")
	publisher := ingest.NewPublisher(&fakeProducer{err: refused}, spansTopic, testBudget)

	err := publisher.PublishTraces(
		context.Background(), identity(), time.Now(), []tracesplit.Group{groupFor("aa", "")},
	)
	if !errors.Is(err, refused) {
		t.Fatalf("PublishTraces() = %v, want the broker failure: the request must not be acked", err)
	}
}

// stallingProducer never answers until the caller's context ends, which is what
// franz-go does while its buffer is full and the broker is behind.
type stallingProducer struct {
	calls atomic.Int64
}

func (s *stallingProducer) ProduceSync(ctx context.Context, records ...*kgo.Record) kgo.ProduceResults {
	s.calls.Add(1)
	<-ctx.Done()
	results := make(kgo.ProduceResults, 0, len(records))
	for _, record := range records {
		results = append(results, kgo.ProduceResult{Record: record, Err: ctx.Err()})
	}
	return results
}

// partialProducer fails the first record of the first call and takes everything
// afterwards, which is the one case a retry here is for.
type partialProducer struct {
	calls [][]*kgo.Record
}

func (p *partialProducer) ProduceSync(_ context.Context, records ...*kgo.Record) kgo.ProduceResults {
	p.calls = append(p.calls, records)
	results := make(kgo.ProduceResults, 0, len(records))
	for index, record := range records {
		result := kgo.ProduceResult{Record: record}
		if len(p.calls) == 1 && index == 0 {
			result.Err = errors.New("kafka: not leader for partition")
		}
		results = append(results, result)
	}
	return results
}

// countingFailure fails everything, and counts how often it was asked to.
type countingFailure struct {
	calls int
}

func (c *countingFailure) ProduceSync(_ context.Context, records ...*kgo.Record) kgo.ProduceResults {
	c.calls++
	results := make(kgo.ProduceResults, 0, len(records))
	for _, record := range records {
		results = append(results, kgo.ProduceResult{
			Record: record,
			Err:    errors.New("kafka: broker unreachable"),
		})
	}
	return results
}

func TestPublishTracesGivesUpOnItsBudgetRatherThanBlocking(t *testing.T) {
	producer := &stallingProducer{}
	publisher := ingest.NewPublisher(producer, spansTopic, testBudget)

	done := make(chan error, 1)
	go func() {
		done <- publisher.PublishTraces(
			context.Background(), identity(), time.Now(), []tracesplit.Group{groupFor("aa", "")},
		)
	}()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("PublishTraces() = nil; a publish that never landed must be reported, not acked")
		}
	case <-time.After(testBudget * 10):
		t.Fatal("PublishTraces blocked past its budget: a stuck broker must not pin a handler goroutine")
	}
	if calls := producer.calls.Load(); calls != 1 {
		t.Errorf("produced %d times, want 1: a publish that landed nothing must not be retried here", calls)
	}
}

func TestPublishTracesDoesNotRetryWhenNothingWasPublished(t *testing.T) {
	producer := &countingFailure{}
	publisher := ingest.NewPublisher(producer, spansTopic, testBudget)

	err := publisher.PublishTraces(
		context.Background(), identity(), time.Now(),
		[]tracesplit.Group{groupFor("aa", ""), groupFor("bb", "")},
	)
	if err == nil {
		t.Fatal("PublishTraces() = nil, want the broker failure")
	}
	if producer.calls != 1 {
		t.Errorf("produced %d times, want 1: retrying a wholly failed batch only multiplies work "+
			"on a broker that is already struggling, and the client retries underneath us", producer.calls)
	}
}

func TestPublishTracesRetriesOnlyTheRecordsThatFailed(t *testing.T) {
	producer := &partialProducer{}
	publisher := ingest.NewPublisher(producer, spansTopic, testBudget)

	err := publisher.PublishTraces(
		context.Background(), identity(), time.Now(),
		[]tracesplit.Group{groupFor("aa", ""), groupFor("bb", "")},
	)
	if err != nil {
		t.Fatalf("PublishTraces() = %v, want the retry to have carried the batch", err)
	}
	if len(producer.calls) != 2 {
		t.Fatalf("produced %d times, want 2: the record that failed had to be retried", len(producer.calls))
	}
	if len(producer.calls[1]) != 1 {
		t.Errorf("retry carried %d records, want only the 1 that failed: republishing what already "+
			"landed would duplicate it", len(producer.calls[1]))
	}
}
