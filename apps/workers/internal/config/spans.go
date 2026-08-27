package config

import (
	"errors"
	"time"
)

// SpanIngest is the trace-assembly pipeline's settings: which topic it reads
// and how it groups rows into inserts.
//
// The two batch limits exist together on purpose. MaxRows keeps one insert to a
// size ClickHouse merges cheaply; MaxDelay bounds how long a row waits for
// company, which is what holds the span-to-queryable budget when a tenant is
// quiet enough that MaxRows alone would never be reached.
type SpanIngest struct {
	// Mirrors the receiver's RECEIVER_SPANS_TOPIC. The two must name the same
	// topic; they are separate variables so each service is configured alone.
	Topic         string        `env:"KAFKA_SPANS_TOPIC" envDefault:"spans-raw"`
	ConsumerGroup string        `env:"WORKERS_CONSUMER_GROUP" envDefault:"nexovue-workers"`
	MaxRows       int           `env:"WORKERS_SPANS_BATCH_MAX_ROWS" envDefault:"5000"`
	MaxDelay      time.Duration `env:"WORKERS_SPANS_BATCH_MAX_DELAY" envDefault:"2s"`

	// DLQTopic parks messages that cannot be decoded. The topic is created by
	// deploy/kafka/topics.sh, which is also where its partition count lives.
	DLQTopic string `env:"WORKERS_SPANS_DLQ_TOPIC" envDefault:"spans-dlq"`
}

func (s SpanIngest) validate() error {
	return errors.Join(
		notBlank("KAFKA_SPANS_TOPIC", s.Topic),
		notBlank("WORKERS_CONSUMER_GROUP", s.ConsumerGroup),
		notBlank("WORKERS_SPANS_DLQ_TOPIC", s.DLQTopic),
		differentValues("WORKERS_SPANS_DLQ_TOPIC", s.DLQTopic, "KAFKA_SPANS_TOPIC", s.Topic),
		positiveCount("WORKERS_SPANS_BATCH_MAX_ROWS", s.MaxRows),
		positiveDuration("WORKERS_SPANS_BATCH_MAX_DELAY", s.MaxDelay),
	)
}
