package config

import (
	"errors"
	"time"
)

// ProfileIngest is the profile-ingest pipeline's settings: which topic it reads
// nexovue.v1.ProfileSamples from, how large a batch is, and where an undecodable
// message is parked.
type ProfileIngest struct {
	// Topic is the one the receiver's profiles door publishes to.
	Topic string `env:"KAFKA_PROFILES_TOPIC" envDefault:"profiles-raw"`

	// ConsumerGroup is profiles' own, so a slow profile write never holds back the
	// spans, logs and metrics groups (F17, NFR1).
	ConsumerGroup string `env:"WORKERS_PROFILES_CONSUMER_GROUP" envDefault:"nexovue-workers-profiles"`

	MaxMessages int `env:"WORKERS_PROFILES_BATCH_MAX_MESSAGES" envDefault:"2000"`
	// MaxRows bounds the rows held before an insert: one message carries every
	// sample a Collector batched, so the message bound says nothing about memory.
	MaxRows  int           `env:"WORKERS_PROFILES_BATCH_MAX_ROWS" envDefault:"20000"`
	MaxDelay time.Duration `env:"WORKERS_PROFILES_BATCH_MAX_DELAY" envDefault:"2s"`

	// MaxBytes bounds one fetch, and so what the consumer holds decompressed, a
	// quarter of the other pipelines' bound. A profile message is the receiver's
	// request with every stack written out, and profiles compress far better than
	// the other signals: at 16 MiB compressed a flood of them decompressed past
	// the worker's memory cap (F17, measured 2026-09-19).
	MaxBytes int `env:"WORKERS_PROFILES_BATCH_MAX_BYTES" envDefault:"4194304"`

	DLQTopic string `env:"WORKERS_PROFILES_DLQ_TOPIC" envDefault:"profiles-dlq"`
}

func (p ProfileIngest) validate() error {
	return errors.Join(
		notBlank("KAFKA_PROFILES_TOPIC", p.Topic),
		notBlank("WORKERS_PROFILES_CONSUMER_GROUP", p.ConsumerGroup),
		notBlank("WORKERS_PROFILES_DLQ_TOPIC", p.DLQTopic),
		differentValues("WORKERS_PROFILES_DLQ_TOPIC", p.DLQTopic, "KAFKA_PROFILES_TOPIC", p.Topic),
		positiveCount("WORKERS_PROFILES_BATCH_MAX_MESSAGES", p.MaxMessages),
		positiveCount("WORKERS_PROFILES_BATCH_MAX_ROWS", p.MaxRows),
		positiveDuration("WORKERS_PROFILES_BATCH_MAX_DELAY", p.MaxDelay),
		positiveCount("WORKERS_PROFILES_BATCH_MAX_BYTES", p.MaxBytes),
	)
}
