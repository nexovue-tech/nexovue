package config

import (
	"errors"
	"time"
)

// MetricIngest is the metric-ingest pipeline's settings: which topic it reads
// customer-sent OTLP metrics from and how it groups rows into inserts.
//
// The two batch limits exist together for the same reason they do on spans:
// MaxRows keeps one insert to a size ClickHouse merges cheaply, MaxDelay bounds
// how long a row waits for company when a tenant is quiet.
//
// One batch here fans out to as many as five tables, so MaxRows counts messages
// rather than rows and the row count per insert is whatever those messages
// carried.
type MetricIngest struct {
	// Mirrors the receiver's KAFKA_METRICS_TOPIC. The two must name the same
	// topic; they are separate variables so each service is configured alone.
	Topic string `env:"KAFKA_METRICS_TOPIC" envDefault:"metrics-raw"`

	// Its own group, not the span pipeline's. Two members of one consumer group
	// subscribing to different topics make the assignment depend on which
	// member the coordinator asks, which is a rebalance storm waiting for a
	// deployment that splits the pipelines apart.
	ConsumerGroup string `env:"WORKERS_METRICS_CONSUMER_GROUP" envDefault:"nexovue-workers-metrics"`

	MaxRows  int           `env:"WORKERS_METRICS_BATCH_MAX_ROWS" envDefault:"5000"`
	MaxDelay time.Duration `env:"WORKERS_METRICS_BATCH_MAX_DELAY" envDefault:"2s"`

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
		positiveCount("WORKERS_METRICS_BATCH_MAX_ROWS", m.MaxRows),
		positiveDuration("WORKERS_METRICS_BATCH_MAX_DELAY", m.MaxDelay),
	)
}
