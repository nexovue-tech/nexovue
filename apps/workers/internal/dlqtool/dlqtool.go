// Package dlqtool reads the dead-letter topics and puts a parked record back
// where it came from (group 9, C5; docs/operations/dead-letters.md).
//
// A parked record is never drained automatically (`pipeline/dlq`): getting one
// out is a deliberate act, after its cause is fixed. This is that act's tool.
package dlqtool

import (
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
)

// ErrNotReplayable is a parked record that cannot go back on a raw topic.
var ErrNotReplayable = errors.New("not replayable")

// Parked is one dead-letter record, as its headers describe it.
type Parked struct {
	Offset          int64
	Reason          string
	SourceTopic     string
	SourcePartition int32
	SourceOffset    int64
	ParkedAt        time.Time
	ConsumerGroup   string
	TenantID        string
	ProjectID       string
	IngestID        string
	Payload         string
}

// Describe reads a parked record's headers. A header that is absent or does
// not parse leaves its field zero; the record is still worth listing.
func Describe(record *kgo.Record) Parked {
	header := func(name string) string {
		for _, h := range record.Headers {
			if h.Key == name {
				return string(h.Value)
			}
		}
		return ""
	}
	partition, _ := strconv.ParseInt(header(dlq.HeaderPartition), 10, 32)
	offset, _ := strconv.ParseInt(header(dlq.HeaderOffset), 10, 64)
	parkedAt, _ := time.Parse(time.RFC3339Nano, header(dlq.HeaderParkedAt))
	return Parked{
		Offset:          record.Offset,
		Reason:          header(dlq.HeaderReason),
		SourceTopic:     header(dlq.HeaderTopic),
		SourcePartition: int32(partition),
		SourceOffset:    offset,
		ParkedAt:        parkedAt,
		ConsumerGroup:   header(dlq.HeaderConsumer),
		TenantID:        header(dlq.HeaderTenantID),
		ProjectID:       header(dlq.HeaderProjectID),
		IngestID:        header(dlq.HeaderIngestID),
		Payload:         header(dlq.HeaderPayload),
	}
}

// Replay is the record to produce to put a parked one back: its original key
// and bytes, on its source topic. It goes through every stage again — masking
// first — as any record does.
func Replay(record *kgo.Record) (*kgo.Record, error) {
	parked := Describe(record)
	switch {
	case parked.Payload == dlq.PayloadRow:
		// A row parked after masking: a stored row as JSON, not an envelope.
		return nil, fmt.Errorf("offset %d is a masked row, not a raw record: %w",
			parked.Offset, ErrNotReplayable)
	case parked.SourceTopic == "":
		return nil, fmt.Errorf("offset %d names no source topic: %w", parked.Offset, ErrNotReplayable)
	}
	return &kgo.Record{Topic: parked.SourceTopic, Key: record.Key, Value: record.Value}, nil
}
