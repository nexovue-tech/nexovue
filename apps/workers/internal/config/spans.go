package config

import (
	"errors"
	"time"
)

// SpanIngest is the trace-assembly pipeline's settings: which topic it reads
// and how it groups rows into inserts.
//
// The two batch limits exist together on purpose. MaxMessages bounds how much
// the loop holds at once; MaxDelay bounds how long a span waits for company,
// which is what holds the span-to-queryable budget when a tenant is quiet
// enough that MaxMessages alone would never be reached.
//
// Unlike logs and metrics, this pipeline has no row bound, because its spans do
// not go straight into an insert — they enter the sampling buffer, which is
// capped separately by WORKERS_SAMPLING_MAX_TRACES.
type SpanIngest struct {
	// Mirrors the receiver's RECEIVER_SPANS_TOPIC. The two must name the same
	// topic; they are separate variables so each service is configured alone.
	Topic         string `env:"KAFKA_SPANS_TOPIC" envDefault:"spans-raw"`
	ConsumerGroup string `env:"WORKERS_CONSUMER_GROUP" envDefault:"nexovue-workers"`
	// Messages, not rows. One message carries many spans, so this bounds how
	// many the loop holds, never how many rows an insert writes. It was called
	// MaxRows and counted messages, which is how a "5,000-row" batch turned out
	// to hold fifty thousand.
	MaxMessages int           `env:"WORKERS_SPANS_BATCH_MAX_MESSAGES" envDefault:"5000"`
	MaxDelay    time.Duration `env:"WORKERS_SPANS_BATCH_MAX_DELAY" envDefault:"2s"`

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
		positiveCount("WORKERS_SPANS_BATCH_MAX_MESSAGES", s.MaxMessages),
		positiveDuration("WORKERS_SPANS_BATCH_MAX_DELAY", s.MaxDelay),
	)
}
