package usage

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

const testTopic = "spans-raw"

// recordedStore models the SQL in postgres.go: one transaction that advances
// the offsets and adds the counters, where an offset that does not advance
// updates nothing and gates the counters out with it.
//
// Modelling it here rather than mocking it is the point — the replay tests
// below are about the protocol between the pipeline and that transaction, and
// the SQL itself is proved against the running Postgres.
type recordedStore struct {
	counted map[int32]int64
	rows    map[string]Counts

	// transactions counts committed transactions, so a test can tell "applied
	// twice, added once" from "applied once".
	transactions int

	// loseAcknowledgement makes the next Apply commit and then report failure,
	// which is the one case a retry could double a customer's usage.
	loseAcknowledgement bool
}

func newRecordedStore() *recordedStore {
	return &recordedStore{counted: make(map[int32]int64), rows: make(map[string]Counts)}
}

func (s *recordedStore) CountedThrough(context.Context) (map[int32]int64, error) {
	counted := make(map[int32]int64, len(s.counted))
	for partition, offset := range s.counted {
		counted[partition] = offset
	}
	return counted, nil
}

func (s *recordedStore) Apply(_ context.Context, rows []Row, offsets map[int32]int64) (Applied, error) {
	advanced := 0
	for partition, offset := range offsets {
		if counted, known := s.counted[partition]; known && offset <= counted {
			continue
		}
		s.counted[partition] = offset
		advanced++
	}
	if advanced == 0 {
		return Applied{AlreadyCounted: true}, nil
	}

	for _, row := range rows {
		key := fmt.Sprintf("%s/%s/%s", row.OrgID, row.ProjectID, row.Day.Format(time.DateOnly))
		total := s.rows[key]
		total.add(row.Counts)
		s.rows[key] = total
	}
	s.transactions++

	if s.loseAcknowledgement {
		s.loseAcknowledgement = false
		return Applied{}, errors.New("connection lost after the commit")
	}
	return Applied{Written: len(rows)}, nil
}

// spans reads back what the store holds for the day under test.
func (s *recordedStore) spans() int64 {
	key := fmt.Sprintf("%s/%s/%s", testOrg, testProject, testReceivedAt.Format(time.DateOnly))
	return s.rows[key].Spans
}

// retryTwice is the consume loop's retry budget in miniature: enough attempts
// to exercise a lost acknowledgement, few enough to fail a test quickly.
func retryTwice(ctx context.Context, _ string, op func(context.Context) error) error {
	err := op(ctx)
	if err == nil {
		return nil
	}
	return op(ctx)
}

func testCounting(store Store) *counting {
	return newCounting(Spans, store, retryTwice,
		slog.New(slog.NewTextHandler(io.Discard, nil)))
}

// batch is one poll's worth of records on one partition, starting at offset.
func batch(t *testing.T, partition int32, offset int64, spansEach ...int) []*kgo.Record {
	t.Helper()

	records := make([]*kgo.Record, 0, len(spansEach))
	for i, spans := range spansEach {
		records = append(records, &kgo.Record{
			Topic:     testTopic,
			Partition: partition,
			Offset:    offset + int64(i),
			Value:     spanMessage(t, spans, testReceivedAt),
		})
	}
	return records
}

func absorbAndFlush(t *testing.T, counter *counting, records []*kgo.Record) {
	t.Helper()

	ctx := context.Background()
	if err := counter.absorb(ctx, records, testReceivedAt); err != nil {
		t.Fatalf("absorbing: %v", err)
	}
	if err := counter.flush(ctx); err != nil {
		t.Fatalf("flushing: %v", err)
	}
}

// A redelivery inside one process — a poll replay, or a rebalance handing back
// a partition — must not be counted again.
func TestARedeliveredBatchIsNotCountedTwice(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)
	records := batch(t, 0, 100, 3, 4)

	absorbAndFlush(t, counter, records)
	absorbAndFlush(t, counter, records)

	if got := store.spans(); got != 7 {
		t.Errorf("spans = %d, want 7: the same two messages were counted twice", got)
	}
}

// The case the durable half exists for: the process died between the flush and
// the Kafka commit, so a new process reads the same records from the broker.
// Its in-memory watermark is empty; only the store knows.
func TestAReplayAfterARestartIsNotCountedTwice(t *testing.T) {
	store := newRecordedStore()
	records := batch(t, 0, 100, 3, 4)

	absorbAndFlush(t, testCounting(store), records)
	// A new process: fresh dedupe, fresh meter, same store.
	absorbAndFlush(t, testCounting(store), records)

	if got := store.spans(); got != 7 {
		t.Errorf("spans = %d, want 7: a restart replayed the batch into the counters", got)
	}
	if store.transactions != 1 {
		t.Errorf("transactions = %d, want 1: the replay was written rather than recognised",
			store.transactions)
	}
}

// A restart part way through a batch: offsets 100-101 were flushed, 102-103
// were not. The replay must count the second half and only the second half.
func TestAPartialReplayCountsOnlyWhatWasNotStored(t *testing.T) {
	store := newRecordedStore()
	absorbAndFlush(t, testCounting(store), batch(t, 0, 100, 3, 4))

	// The next process polls the whole window again, the broker having been
	// committed only through offset 101.
	absorbAndFlush(t, testCounting(store), batch(t, 0, 100, 3, 4, 5, 6))

	if got := store.spans(); got != 18 {
		t.Errorf("spans = %d, want 18 (3+4 once, then 5+6)", got)
	}
}

// The one window a two-store design cannot close, and a transaction can: the
// commit landed and the acknowledgement did not, so the write is retried.
func TestALostAcknowledgementIsNotCountedTwice(t *testing.T) {
	store := newRecordedStore()
	store.loseAcknowledgement = true
	counter := testCounting(store)

	absorbAndFlush(t, counter, batch(t, 0, 100, 3, 4))

	if got := store.spans(); got != 7 {
		t.Errorf("spans = %d, want 7: the retry added the same counters again", got)
	}
	if store.transactions != 1 {
		t.Errorf("transactions = %d, want 1", store.transactions)
	}
}

// A day's row is written many times as the day goes on. The update has to add,
// not replace, or a restart at noon erases the morning.
func TestASecondFlushAddsToTheDayRatherThanReplacingIt(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)

	absorbAndFlush(t, counter, batch(t, 0, 100, 3, 4))
	absorbAndFlush(t, counter, batch(t, 0, 102, 5))

	if got := store.spans(); got != 12 {
		t.Errorf("spans = %d, want 12: the second flush replaced the first", got)
	}
}

// Partitions settle independently, and one that has not moved must not hold
// back or re-count another.
func TestPartitionsAreCountedThroughSeparately(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)

	absorbAndFlush(t, counter, batch(t, 0, 100, 3))
	absorbAndFlush(t, counter, batch(t, 1, 100, 4))
	// Partition 0 replayed while partition 1 carries new records.
	absorbAndFlush(t, testCounting(store), append(batch(t, 0, 100, 3), batch(t, 1, 101, 5)...))

	if got := store.spans(); got != 12 {
		t.Errorf("spans = %d, want 12 (3 + 4 + 5)", got)
	}
}

// A message the meter cannot read still moves the watermark: its offset must
// not be counted again on the next poll, and it must not stall the ones after
// it either.
func TestAnUnreadableMessageStillAdvancesTheWatermark(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)
	records := []*kgo.Record{
		{Topic: testTopic, Partition: 0, Offset: 100, Value: []byte("not an envelope")},
		{Topic: testTopic, Partition: 0, Offset: 101, Value: spanMessage(t, 6, testReceivedAt)},
	}

	absorbAndFlush(t, counter, records)

	if got := store.counted[0]; got != 101 {
		t.Errorf("counted through offset %d, want 101", got)
	}
	if got := store.spans(); got != 6 {
		t.Errorf("spans = %d, want 6", got)
	}
	if counter.totals.undecodable != 1 {
		t.Errorf("undecodable = %d, want 1", counter.totals.undecodable)
	}
}

// Nothing counted means nothing written: a quiet topic must not write a row of
// zeros every flush interval.
func TestAFlushWithNothingToCountWritesNothing(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)

	if err := counter.flush(context.Background()); err != nil {
		t.Fatalf("flushing: %v", err)
	}
	if store.transactions != 0 {
		t.Errorf("transactions = %d, want 0", store.transactions)
	}
}

// The flush is what makes a batch storable. Until it happens the loop must hold
// the offsets, or a crash would lose counters the broker no longer has.
func TestOffsetsAreHeldUntilTheCountersAreWritten(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)
	ctx := context.Background()

	held := counter.flushes
	if err := counter.absorb(ctx, batch(t, 0, 100, 3), testReceivedAt); err != nil {
		t.Fatalf("absorbing: %v", err)
	}
	if counter.flushes != held {
		t.Fatal("absorbing a batch marked it stored before anything was written")
	}
	if err := counter.flush(ctx); err != nil {
		t.Fatalf("flushing: %v", err)
	}
	if counter.flushes <= held {
		t.Error("a committed flush did not release the batch's offsets")
	}
}
