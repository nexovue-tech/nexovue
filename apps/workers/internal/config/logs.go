package config

import (
	"errors"
	"time"
)

// LogIngest is the log-writing pipeline's settings: which topic it reads and
// how it groups rows into inserts.
//
// The two batch limits exist together on purpose. MaxRows keeps one insert to a
// size ClickHouse merges cheaply; MaxDelay bounds how long a row waits for
// company, which is what holds the log-to-queryable budget when a tenant is
// quiet enough that MaxRows alone would never be reached.
type LogIngest struct {
	// Mirrors the receiver's RECEIVER_LOGS_TOPIC. The two must name the same
	// topic; they are separate variables so each service is configured alone.
	Topic string `env:"KAFKA_LOGS_TOPIC" envDefault:"logs-raw"`

	// Its own group, not the span pipeline's. Two members of one consumer group
	// subscribing to different topics make the assignment depend on which
	// member the coordinator asks, which is a rebalance storm waiting for a
	// deployment that splits the pipelines apart.
	//
	// metric-ingest was given its own group for exactly this reason and logs
	// were left sharing the span group, which is the same defect in the second
	// of the two places it exists.
	ConsumerGroup string        `env:"WORKERS_LOGS_CONSUMER_GROUP" envDefault:"nexovue-workers-logs"`
	MaxRows       int           `env:"WORKERS_LOGS_BATCH_MAX_ROWS" envDefault:"5000"`
	MaxDelay      time.Duration `env:"WORKERS_LOGS_BATCH_MAX_DELAY" envDefault:"2s"`

	// DLQTopic parks messages that cannot be decoded. The topic is created by
	// deploy/kafka/topics.sh, which is also where its partition count lives.
	DLQTopic string `env:"WORKERS_LOGS_DLQ_TOPIC" envDefault:"logs-dlq"`
}

func (l LogIngest) validate() error {
	return errors.Join(
		notBlank("KAFKA_LOGS_TOPIC", l.Topic),
		notBlank("WORKERS_LOGS_CONSUMER_GROUP", l.ConsumerGroup),
		notBlank("WORKERS_LOGS_DLQ_TOPIC", l.DLQTopic),
		differentValues("WORKERS_LOGS_DLQ_TOPIC", l.DLQTopic, "KAFKA_LOGS_TOPIC", l.Topic),
		positiveCount("WORKERS_LOGS_BATCH_MAX_ROWS", l.MaxRows),
		positiveDuration("WORKERS_LOGS_BATCH_MAX_DELAY", l.MaxDelay),
	)
}
