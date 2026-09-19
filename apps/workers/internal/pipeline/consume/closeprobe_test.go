//go:build storeprobe

package consume

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
	"github.com/twmb/franz-go/pkg/kmsg"
)

// A pipeline whose store refuses a write must let the process leave.
//
// Run returning an error is the worker's signal to exit so it can be restarted.
// But the batch that failed was polled under BlockRebalanceOnPoll, and leaving
// the group is a rebalance: if Run returns without AllowRebalance, Close waits in
// LeaveGroup for ever, and the worker sits unhealthy with ingest stopped. That
// is what a ClickHouse MEMORY_LIMIT_EXCEEDED did on 2026-09-19.
//
// Behind a build tag: it needs a running Kafka. Run it with
//
//	PROBE_KAFKA_BROKERS=127.0.0.1:19092 go test -tags storeprobe -count=1 \
//		-run TestAFailedWriteStillLetsThePipelineClose ./internal/pipeline/consume/
const closeProbeTimeout = 20 * time.Second

func TestAFailedWriteStillLetsThePipelineClose(t *testing.T) {
	brokers := os.Getenv("PROBE_KAFKA_BROKERS")
	if brokers == "" {
		t.Fatal("PROBE_KAFKA_BROKERS is not set; this probe needs a live Kafka")
	}
	seeds := strings.Split(brokers, ",")
	topic := fmt.Sprintf("close-probe-%d", time.Now().UnixNano())
	admin := probeTopics(t, seeds, topic, topic+"-dlq")

	record := &kgo.Record{Topic: topic, Value: []byte("one message")}
	if err := admin.ProduceSync(context.Background(), record).FirstErr(); err != nil {
		t.Fatalf("producing: %v", err)
	}

	loop, err := Dial[int](Settings{
		Name:          "close-probe",
		Brokers:       seeds,
		Topic:         topic,
		ConsumerGroup: topic + "-group",
		DLQTopic:      topic + "-dlq",
		MaxMessages:   1,
		MaxDelay:      100 * time.Millisecond,
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("dialling: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), closeProbeTimeout)
	defer cancel()
	if err := loop.Run(ctx, refusingStore{}); !errors.Is(err, errStoreRefused) {
		t.Fatalf("Run returned %v, want the store's refusal", err)
	}

	closed := make(chan struct{})
	go func() {
		loop.Close()
		close(closed)
	}()
	select {
	case <-closed:
	case <-time.After(closeProbeTimeout):
		t.Fatalf("Close did not return within %s: the consumer is stuck leaving its group, "+
			"so the worker would never exit and never be restarted", closeProbeTimeout)
	}
}

var errStoreRefused = errors.New("store refused the write")

// refusingStore fails the first batch, as ClickHouse did when it ran out of memory.
type refusingStore struct{}

func (refusingStore) Absorb(context.Context, Polled) (int, error) { return 0, errStoreRefused }
func (refusingStore) Settle(context.Context, time.Time) error     { return nil }
func (refusingStore) Stored(time.Time, int) bool                  { return false }
func (refusingStore) Drain(context.Context, time.Time) error      { return nil }
func (refusingStore) Holding() []slog.Attr                        { return nil }
func (refusingStore) Describe() []slog.Attr                       { return nil }

// probeTopics creates single-partition topics for one test and deletes them after
// it; the development broker does not create topics on first use.
func probeTopics(t *testing.T, seeds []string, names ...string) *kgo.Client {
	t.Helper()
	client, err := kgo.NewClient(kgo.SeedBrokers(seeds...))
	if err != nil {
		t.Fatalf("admin client: %v", err)
	}

	create := kmsg.NewPtrCreateTopicsRequest()
	create.TimeoutMillis = 10_000
	for _, name := range names {
		topic := kmsg.NewCreateTopicsRequestTopic()
		topic.Topic = name
		topic.NumPartitions = 1
		topic.ReplicationFactor = 1
		create.Topics = append(create.Topics, topic)
	}
	if _, err := create.RequestWith(context.Background(), client); err != nil {
		t.Fatalf("creating topics: %v", err)
	}

	t.Cleanup(func() {
		remove := kmsg.NewPtrDeleteTopicsRequest()
		remove.TimeoutMillis = 10_000
		remove.TopicNames = names
		for _, name := range names {
			topic := kmsg.NewDeleteTopicsRequestTopic()
			topic.Topic = &name
			remove.Topics = append(remove.Topics, topic)
		}
		_, _ = remove.RequestWith(context.Background(), client)
		client.Close()
	})
	return client
}
