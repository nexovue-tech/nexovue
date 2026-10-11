package dlqtool

import (
	"errors"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/dlq"
)

func parked(payload string) *kgo.Record {
	headers := []kgo.RecordHeader{
		{Key: dlq.HeaderReason, Value: []byte("decode envelope: unexpected EOF")},
		{Key: dlq.HeaderTopic, Value: []byte("logs-raw")},
		{Key: dlq.HeaderPartition, Value: []byte("2")},
		{Key: dlq.HeaderOffset, Value: []byte("4410")},
		{Key: dlq.HeaderParkedAt, Value: []byte("2026-10-11T12:00:00.5Z")},
		{Key: dlq.HeaderTenantID, Value: []byte("org-1")},
	}
	if payload != "" {
		headers = append(headers, kgo.RecordHeader{Key: dlq.HeaderPayload, Value: []byte(payload)})
	}
	return &kgo.Record{Topic: "logs-dlq", Offset: 7, Key: []byte("org-1"), Value: []byte("raw"), Headers: headers}
}

func TestAParkedRecordIsDescribedFromItsHeaders(t *testing.T) {
	got := Describe(parked(""))

	want := Parked{
		Offset: 7, Reason: "decode envelope: unexpected EOF",
		SourceTopic: "logs-raw", SourcePartition: 2, SourceOffset: 4410,
		ParkedAt: time.Date(2026, 10, 11, 12, 0, 0, 500_000_000, time.UTC),
		TenantID: "org-1",
	}
	if got.Offset != want.Offset || got.Reason != want.Reason || got.SourceTopic != want.SourceTopic ||
		got.SourcePartition != want.SourcePartition || got.SourceOffset != want.SourceOffset ||
		!got.ParkedAt.Equal(want.ParkedAt) || got.TenantID != want.TenantID {
		t.Fatalf("Describe() = %+v\nwant %+v", got, want)
	}
}

func TestAReplayIsTheOriginalBytesOnTheirSourceTopic(t *testing.T) {
	replay, err := Replay(parked(""))
	if err != nil {
		t.Fatal(err)
	}
	if replay.Topic != "logs-raw" || string(replay.Key) != "org-1" || string(replay.Value) != "raw" {
		t.Fatalf("Replay() = %s %q %q", replay.Topic, replay.Key, replay.Value)
	}
}

// A row parked after masking is a stored row as JSON, not an envelope; put on a
// raw topic it would be refused again and parked again.
func TestAMaskedRowIsNotReplayed(t *testing.T) {
	if _, err := Replay(parked(dlq.PayloadRow)); !errors.Is(err, ErrNotReplayable) {
		t.Fatalf("Replay() = %v, want ErrNotReplayable", err)
	}
}
