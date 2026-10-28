package setup

import (
	"context"
	"errors"
	"fmt"
	"strconv"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kerr"
)

// TopicSettings are the knobs a deployment may turn; the defaults are what every install uses.
type TopicSettings struct {
	SpanPartitions  int32
	OtherPartitions int32
	// Seven days of buffer: long enough to replay a weekend's outage. The worker's
	// trustedPastSkew matches it (pipeline/derive/counters.go); raise both together.
	RetentionMillis int64
	// One record is one trace's envelope, so this clears the receiver's 16 MiB body cap plus
	// envelope overhead. If the receiver's cap changes, this changes with it.
	MaxMessageBytes int64
}

// DefaultTopicSettings are every install's sizes. spans-raw is keyed by trace and each partition is
// one assembler's whole world, so its count is the assembler's ceiling — generous now, because
// changing it reshuffles trace ownership.
var DefaultTopicSettings = TopicSettings{
	SpanPartitions:  12,
	OtherPartitions: 4,
	RetentionMillis: 604_800_000,
	MaxMessageBytes: 16_781_312,
}

// maxReplication: three copies survive one broker lost with writes continuing. More brokers add
// capacity, not copies.
const maxReplication = 3

// TopicSpec is one topic as it is created.
type TopicSpec struct {
	Name        string
	Partitions  int32
	Replication int16
	Configs     map[string]string
}

// PlanTopics is every topic nexovue uses, sized for the brokers that exist: as many copies as
// there are brokers, up to three, and an acknowledged write held by all copies but one.
func PlanTopics(brokers int, settings TopicSettings) ([]TopicSpec, error) {
	if brokers < 1 {
		return nil, errors.New("no Kafka broker answered")
	}
	replication := int16(min(brokers, maxReplication))
	minInSync := max(replication-1, 1)
	configs := map[string]string{
		"retention.ms":        strconv.FormatInt(settings.RetentionMillis, 10),
		"max.message.bytes":   strconv.FormatInt(settings.MaxMessageBytes, 10),
		"min.insync.replicas": strconv.Itoa(int(minInSync)),
		"compression.type":    "zstd",
	}
	spec := func(name string, partitions int32) TopicSpec {
		return TopicSpec{Name: name, Partitions: partitions, Replication: replication, Configs: configs}
	}
	return []TopicSpec{
		spec("spans-raw", settings.SpanPartitions),
		spec("logs-raw", settings.OtherPartitions),
		spec("metrics-raw", settings.OtherPartitions),
		// Profiles carry nexovue.v1.ProfileSamples, not OTLP (ADR-015).
		spec("profiles-raw", settings.OtherPartitions),
		// Poison messages land here rather than blocking a partition (P1-20).
		spec("spans-dlq", 1),
		spec("logs-dlq", 1),
		spec("metrics-dlq", 1),
		spec("profiles-dlq", 1),
	}, nil
}

// TopicResult says what happened to one topic.
type TopicResult struct {
	Name    string
	Created bool
}

// EnsureTopics creates each planned topic that does not exist. An existing topic is left as it
// is: changing a live topic's partitions reshuffles its keys, which is never a setup step.
func EnsureTopics(ctx context.Context, admin *kadm.Client, settings TopicSettings) ([]TopicResult, error) {
	brokers, err := admin.ListBrokers(ctx)
	if err != nil {
		return nil, fmt.Errorf("list Kafka brokers: %w", err)
	}
	plan, err := PlanTopics(len(brokers), settings)
	if err != nil {
		return nil, err
	}
	results := make([]TopicResult, 0, len(plan))
	for _, topic := range plan {
		configs := make(map[string]*string, len(topic.Configs))
		for key, value := range topic.Configs {
			configs[key] = &value
		}
		created, err := admin.CreateTopic(ctx, topic.Partitions, topic.Replication, configs, topic.Name)
		switch {
		case errors.Is(err, kerr.TopicAlreadyExists) || errors.Is(created.Err, kerr.TopicAlreadyExists):
			results = append(results, TopicResult{Name: topic.Name})
		case err != nil:
			return results, fmt.Errorf("create topic %s: %w", topic.Name, err)
		case created.Err != nil:
			return results, fmt.Errorf("create topic %s: %w", topic.Name, created.Err)
		default:
			results = append(results, TopicResult{Name: topic.Name, Created: true})
		}
	}
	return results, nil
}
