package selfmetrics_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/twmb/franz-go/pkg/kadm"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/selfmetrics"
)

type fakeKafka struct {
	lags       kadm.DescribedGroupLags
	start, end kadm.ListedOffsets
	err        error
}

func (f fakeKafka) Lag(context.Context, ...string) (kadm.DescribedGroupLags, error) {
	return f.lags, f.err
}

func (f fakeKafka) ListStartOffsets(context.Context, ...string) (kadm.ListedOffsets, error) {
	return f.start, f.err
}

func (f fakeKafka) ListEndOffsets(context.Context, ...string) (kadm.ListedOffsets, error) {
	return f.end, f.err
}

var sources = []selfmetrics.Source{
	{Group: "nexovue-workers-logs", DeadLetterTopic: "logs-dlq"},
	{Group: "nexovue-workers-logs-usage", DeadLetterTopic: "logs-dlq"},
}

func offsets(topic string, byPartition map[int32]int64) kadm.ListedOffsets {
	listed := kadm.ListedOffsets{topic: {}}
	for partition, offset := range byPartition {
		listed[topic][partition] = kadm.ListedOffset{Topic: topic, Partition: partition, Offset: offset}
	}
	return listed
}

func TestLagIsSummedPerGroupAndTopicAndDeadLettersAreCounted(t *testing.T) {
	kafka := fakeKafka{
		lags: kadm.DescribedGroupLags{
			"nexovue-workers-logs": {Group: "nexovue-workers-logs", Lag: kadm.GroupLag{"logs-raw": {
				0: {Topic: "logs-raw", Partition: 0, Lag: 40},
				1: {Topic: "logs-raw", Partition: 1, Lag: 2},
			}}},
			"nexovue-workers-logs-usage": {Group: "nexovue-workers-logs-usage", Lag: kadm.GroupLag{"logs-raw": {
				0: {Topic: "logs-raw", Partition: 0, Lag: 0},
			}}},
		},
		start: offsets("logs-dlq", map[int32]int64{0: 3}),
		end:   offsets("logs-dlq", map[int32]int64{0: 10}),
	}

	want := `
# HELP nexovue_worker_consumer_lag_records Records written to a topic that a consumer group has not yet committed.
# TYPE nexovue_worker_consumer_lag_records gauge
nexovue_worker_consumer_lag_records{group="nexovue-workers-logs",topic="logs-raw"} 42
nexovue_worker_consumer_lag_records{group="nexovue-workers-logs-usage",topic="logs-raw"} 0
# HELP nexovue_worker_dead_letter_records Records parked in a dead-letter topic and still retained.
# TYPE nexovue_worker_dead_letter_records gauge
nexovue_worker_dead_letter_records{topic="logs-dlq"} 7
# HELP nexovue_worker_kafka_admin_up Whether the last scrape could read offsets from Kafka.
# TYPE nexovue_worker_kafka_admin_up gauge
nexovue_worker_kafka_admin_up 1
`
	collector := selfmetrics.New(kafka, sources)
	if err := testutil.CollectAndCompare(collector, strings.NewReader(want)); err != nil {
		t.Fatal(err)
	}
}

// Absent, not zero: a lag of 0 reads as "caught up", which is the opposite of
// "we could not tell".
func TestAnUnreadableKafkaReportsNoLagRatherThanZero(t *testing.T) {
	collector := selfmetrics.New(fakeKafka{err: errors.New("broker down")}, sources)

	want := `
# HELP nexovue_worker_kafka_admin_up Whether the last scrape could read offsets from Kafka.
# TYPE nexovue_worker_kafka_admin_up gauge
nexovue_worker_kafka_admin_up 0
`
	if err := testutil.CollectAndCompare(collector, strings.NewReader(want)); err != nil {
		t.Fatal(err)
	}
}

// A partition whose lag kadm could not compute (-1) is left out, not counted.
func TestAPartitionWithoutALagIsLeftOut(t *testing.T) {
	kafka := fakeKafka{
		lags: kadm.DescribedGroupLags{"nexovue-workers-logs": {Lag: kadm.GroupLag{"logs-raw": {
			0: {Lag: -1, Err: errors.New("no commit")},
			1: {Lag: 5},
		}}}},
		start: kadm.ListedOffsets{}, end: kadm.ListedOffsets{},
	}
	want := `
# HELP nexovue_worker_consumer_lag_records Records written to a topic that a consumer group has not yet committed.
# TYPE nexovue_worker_consumer_lag_records gauge
nexovue_worker_consumer_lag_records{group="nexovue-workers-logs",topic="logs-raw"} 5
`
	collector := selfmetrics.New(kafka, sources[:1])
	if err := testutil.CollectAndCompare(collector, strings.NewReader(want),
		"nexovue_worker_consumer_lag_records"); err != nil {
		t.Fatal(err)
	}
}
