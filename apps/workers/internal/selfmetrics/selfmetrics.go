// Package selfmetrics is what a monitor reads about the worker's Kafka side
// (ADR-021): how far each consumer group is behind, and how much sits parked in
// the dead-letter topics.
//
// Both are measured on the broker, not inside the pipelines: lag is the topics'
// end offsets minus each group's committed offsets, and dead-letter depth the
// end minus the start of each dead-letter topic — what is still retained there,
// whichever process parked it. Computed when scraped, under a budget, so the
// ingest path never waits on it.
package selfmetrics

import (
	"context"
	"slices"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/twmb/franz-go/pkg/kadm"
)

// scrapeBudget bounds one scrape's calls to Kafka, below a monitor's own scrape
// timeout, so a slow broker costs a missing reading and not a failed scrape.
const scrapeBudget = 2 * time.Second

// Kafka is the part of kadm.Client this reads.
type Kafka interface {
	Lag(ctx context.Context, groups ...string) (kadm.DescribedGroupLags, error)
	ListStartOffsets(ctx context.Context, topics ...string) (kadm.ListedOffsets, error)
	ListEndOffsets(ctx context.Context, topics ...string) (kadm.ListedOffsets, error)
}

// Source is one consumer group this process runs, and where it parks what it
// cannot use.
type Source struct {
	Group           string
	DeadLetterTopic string
}

var (
	lagDesc = prometheus.NewDesc("nexovue_worker_consumer_lag_records",
		"Records written to a topic that a consumer group has not yet committed.",
		[]string{"group", "topic"}, nil)
	deadLetterDesc = prometheus.NewDesc("nexovue_worker_dead_letter_records",
		"Records parked in a dead-letter topic and still retained.", []string{"topic"}, nil)
	adminUpDesc = prometheus.NewDesc("nexovue_worker_kafka_admin_up",
		"Whether the last scrape could read offsets from Kafka.", nil, nil)
)

// Collector reports the sources' lag and dead-letter depth on every scrape.
type Collector struct {
	kafka  Kafka
	groups []string
	topics []string
}

// New returns a collector over sources. Groups and topics are fixed here, from
// configuration, so no label value ever comes from data.
func New(kafka Kafka, sources []Source) *Collector {
	collector := &Collector{kafka: kafka}
	for _, source := range sources {
		if !slices.Contains(collector.groups, source.Group) {
			collector.groups = append(collector.groups, source.Group)
		}
		if !slices.Contains(collector.topics, source.DeadLetterTopic) {
			collector.topics = append(collector.topics, source.DeadLetterTopic)
		}
	}
	return collector
}

// Describe implements prometheus.Collector.
func (c *Collector) Describe(descs chan<- *prometheus.Desc) {
	descs <- lagDesc
	descs <- deadLetterDesc
	descs <- adminUpDesc
}

// Collect implements prometheus.Collector. On any Kafka error it reports the
// admin as down and no lag at all: a lag of 0 would read as "caught up".
func (c *Collector) Collect(metrics chan<- prometheus.Metric) {
	ctx, cancel := context.WithTimeout(context.Background(), scrapeBudget)
	defer cancel()

	lags, err := c.kafka.Lag(ctx, c.groups...)
	if err != nil {
		metrics <- prometheus.MustNewConstMetric(adminUpDesc, prometheus.GaugeValue, 0)
		return
	}
	start, startErr := c.kafka.ListStartOffsets(ctx, c.topics...)
	end, endErr := c.kafka.ListEndOffsets(ctx, c.topics...)
	if startErr != nil || endErr != nil {
		metrics <- prometheus.MustNewConstMetric(adminUpDesc, prometheus.GaugeValue, 0)
		return
	}
	metrics <- prometheus.MustNewConstMetric(adminUpDesc, prometheus.GaugeValue, 1)

	for _, group := range c.groups {
		for topic, partitions := range lags[group].Lag {
			var behind int64
			for _, partition := range partitions {
				// -1 is kadm's "could not compute"; counting it would subtract.
				if partition.Err == nil && partition.Lag >= 0 {
					behind += partition.Lag
				}
			}
			metrics <- prometheus.MustNewConstMetric(
				lagDesc, prometheus.GaugeValue, float64(behind), group, topic)
		}
	}
	for _, topic := range c.topics {
		var parked int64
		for partition, last := range end[topic] {
			first, known := start[topic][partition]
			if known && first.Err == nil && last.Err == nil {
				parked += last.Offset - first.Offset
			}
		}
		metrics <- prometheus.MustNewConstMetric(
			deadLetterDesc, prometheus.GaugeValue, float64(parked), topic)
	}
}
