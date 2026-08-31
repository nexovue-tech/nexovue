package config

import (
	"errors"
	"time"
)

// MetricIngest is the metric-ingest pipeline's settings: which topic it reads
// customer-sent OTLP metrics from and how it groups rows into inserts.
//
// Three limits, each bounding something different. MaxMessages caps how many
// Kafka messages one batch holds. MaxRows caps how many rows are held across
// the five tables before they are inserted, and it is the only one that bounds
// memory: an OTLP message carries as many data points as the sender's exporter
// batched, so a bound of 5000 messages held tens of thousands of points here
// and the worker was OOM-killed at its 1 GiB limit. MaxDelay bounds how long a
// point waits for company when a tenant is quiet.
type MetricIngest struct {
	// Mirrors the receiver's KAFKA_METRICS_TOPIC. The two must name the same
	// topic; they are separate variables so each service is configured alone.
	Topic string `env:"KAFKA_METRICS_TOPIC" envDefault:"metrics-raw"`

	// Its own group, not the span pipeline's. Two members of one consumer group
	// subscribing to different topics make the assignment depend on which
	// member the coordinator asks, which is a rebalance storm waiting for a
	// deployment that splits the pipelines apart.
	ConsumerGroup string `env:"WORKERS_METRICS_CONSUMER_GROUP" envDefault:"nexovue-workers-metrics"`

	// MaxMessages was WORKERS_METRICS_BATCH_MAX_ROWS, which counted messages
	// while claiming to count rows. A deployment that set the old name is now
	// setting the row bound instead, which is why the name changed rather than
	// the meaning being corrected in place.
	MaxMessages int           `env:"WORKERS_METRICS_BATCH_MAX_MESSAGES" envDefault:"5000"`
	MaxRows     int           `env:"WORKERS_METRICS_BATCH_MAX_ROWS" envDefault:"5000"`
	MaxDelay    time.Duration `env:"WORKERS_METRICS_BATCH_MAX_DELAY" envDefault:"2s"`

	// DLQTopic parks messages that cannot be processed. The topic is created by
	// deploy/kafka/topics.sh, which is also where its partition count lives.
	DLQTopic string `env:"WORKERS_METRICS_DLQ_TOPIC" envDefault:"metrics-dlq"`
}

func (m MetricIngest) validate() error {
	return errors.Join(
		notBlank("KAFKA_METRICS_TOPIC", m.Topic),
		notBlank("WORKERS_METRICS_CONSUMER_GROUP", m.ConsumerGroup),
		notBlank("WORKERS_METRICS_DLQ_TOPIC", m.DLQTopic),
		differentValues("WORKERS_METRICS_DLQ_TOPIC", m.DLQTopic, "KAFKA_METRICS_TOPIC", m.Topic),
		positiveCount("WORKERS_METRICS_BATCH_MAX_MESSAGES", m.MaxMessages),
		positiveCount("WORKERS_METRICS_BATCH_MAX_ROWS", m.MaxRows),
		positiveDuration("WORKERS_METRICS_BATCH_MAX_DELAY", m.MaxDelay),
	)
}
