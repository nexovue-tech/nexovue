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

	nexovuev1 "github.com/nexovue-tech/nexovue/packages/contracts/gen/go/nexovue/v1"
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
	counted  map[int32]int64
	received map[int32]time.Time
	rows     map[string]Counts

	// services is the set table: one entry per tenant-day, holding the names
	// recorded for it. Inserting a name already there changes nothing, which is
	// `ON CONFLICT DO NOTHING` and the reason a replayed service is harmless.
	services map[string]map[string]struct{}

	// transactions counts committed transactions, so a test can tell "applied
	// twice, added once" from "applied once".
	transactions int

	// loseAcknowledgement makes the next Apply commit and then report failure,
	// which is the one case a retry could double a customer's usage.
	loseAcknowledgement bool
}

func newRecordedStore() *recordedStore {
	return &recordedStore{
		counted:  make(map[int32]int64),
		received: make(map[int32]time.Time),
		rows:     make(map[string]Counts),
		services: make(map[string]map[string]struct{}),
	}
}

func (s *recordedStore) CountedThrough(context.Context) (map[int32]int64, error) {
	counted := make(map[int32]int64, len(s.counted))
	for partition, offset := range s.counted {
		counted[partition] = offset
	}
	return counted, nil
}

func (s *recordedStore) Apply(
	_ context.Context,
	rows []Row,
	progress map[int32]Progress,
) (Applied, error) {
	advanced := 0
	for partition, reached := range progress {
		if counted, known := s.counted[partition]; known && reached.Offset <= counted {
			continue
		}
		s.counted[partition] = reached.Offset
		// GREATEST, ignoring the null a flush that read nothing new sends.
		if reached.ReceivedThrough.After(s.received[partition]) {
			s.received[partition] = reached.ReceivedThrough
		}
		advanced++
	}
	if advanced == 0 {
		return Applied{AlreadyCounted: true}, nil
	}

	newServices := 0
	for _, row := range rows {
		key := tenantDayKey(row)
		total := s.rows[key]
		total.add(row.Counts)
		s.rows[key] = total

		recorded, held := s.services[key]
		if !held {
			recorded = make(map[string]struct{})
			s.services[key] = recorded
		}
		for _, name := range row.Services {
			if _, already := recorded[name]; already {
				continue
			}
			recorded[name] = struct{}{}
			newServices++
		}
	}
	s.transactions++

	if s.loseAcknowledgement {
		s.loseAcknowledgement = false
		return Applied{}, errors.New("connection lost after the commit")
	}
	return Applied{Written: len(rows), Services: newServices}, nil
}

func tenantDayKey(row Row) string {
	return fmt.Sprintf("%s/%s/%s", row.OrgID, row.ProjectID, row.Day.Format(time.DateOnly))
}

// spans reads back what the store holds for the day under test.
func (s *recordedStore) spans() int64 {
	return s.rows[testDayKey()].Spans
}

// billableServices is the number a customer would be invoiced on for the day
// under test: the size of the set, not a sum of anything.
func (s *recordedStore) billableServices() int {
	return len(s.services[testDayKey()])
}

func testDayKey() string {
	return fmt.Sprintf("%s/%s/%s", testOrg, testProject, testReceivedAt.Format(time.DateOnly))
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

// servicesBatch is one poll's worth of records carrying named services, one
// span each, so a test can watch the set rather than the counters.
func servicesBatch(
	t *testing.T,
	partition int32,
	offset int64,
	receivedAt time.Time,
	servicesNamed ...string,
) []*kgo.Record {
	t.Helper()

	return []*kgo.Record{{
		Topic:     testTopic,
		Partition: partition,
		Offset:    offset,
		Value: envelope(t, nexovuev1.Signal_SIGNAL_SPANS,
			spansFrom(t, 1, servicesNamed...), receivedAt),
	}}
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

// ------------------------------------------------- the billing unit, replayed

// The counters need the offset guard to survive a replay. The set does not: the
// same names arriving again leave it where it was. Both halves are proved here
// because the bill rests on the second one.
func TestAReplayedBatchLeavesTheServiceSetWhereItWas(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)
	records := servicesBatch(t, 0, 100, testReceivedAt, "checkout", "payments")

	absorbAndFlush(t, counter, records)
	absorbAndFlush(t, counter, records)
	// A new process, its in-memory watermark empty, replaying from the broker.
	absorbAndFlush(t, testCounting(store), records)

	if got := store.billableServices(); got != 2 {
		t.Errorf("billable services = %d, want 2: a replay changed the bill", got)
	}
}

// The same services report again every ten seconds all day. The set must not
// grow with the traffic — this is the case a counter would get wrong on the
// first flush, long before any replay.
func TestServicesSeenAgainOnNewRecordsDoNotGrowTheSet(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)

	absorbAndFlush(t, counter, servicesBatch(t, 0, 100, testReceivedAt, "checkout", "payments"))
	absorbAndFlush(t, counter, servicesBatch(t, 0, 101, testReceivedAt, "checkout", "payments"))
	absorbAndFlush(t, counter, servicesBatch(t, 0, 102, testReceivedAt, "checkout", "ledger"))

	if got := store.billableServices(); got != 3 {
		t.Errorf("billable services = %d, want 3 (checkout, payments, ledger)", got)
	}
}

// A day's set is its own. A service that ran yesterday and not today is not
// billable today.
func TestEachDayHasItsOwnServiceSet(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)
	nextDay := testReceivedAt.Add(24 * time.Hour)

	absorbAndFlush(t, counter, servicesBatch(t, 0, 100, testReceivedAt, "checkout", "payments"))
	absorbAndFlush(t, counter, servicesBatch(t, 0, 101, nextDay, "checkout"))

	if got := store.billableServices(); got != 2 {
		t.Errorf("first day services = %d, want 2", got)
	}
	tomorrow := Row{OrgID: testOrg, ProjectID: testProject, Day: nextDay.UTC().Truncate(day)}
	if got := len(store.services[tenantDayKey(tomorrow)]); got != 1 {
		t.Errorf("second day services = %d, want 1", got)
	}
}

// -------------------------------------------------- how far the meter counted

// The receive-time frontier is what says a day has finished being counted.
// Offsets cannot: a meter hours behind is still advancing them.
func TestTheFlushRecordsHowFarTheMeterCountedInReceiveTime(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)
	later := testReceivedAt.Add(2 * time.Hour)

	absorbAndFlush(t, counter, servicesBatch(t, 0, 100, testReceivedAt, "checkout"))
	absorbAndFlush(t, counter, servicesBatch(t, 0, 101, later, "checkout"))

	if got := store.received[0]; !got.Equal(later) {
		t.Errorf("counted through %s, want %s", got, later)
	}
}

// A flush carrying only replays reports no receive time at all, and the stored
// frontier is kept rather than walked back to nothing.
func TestAFlushOfOnlyReplaysDoesNotMoveTheFrontierBack(t *testing.T) {
	store := newRecordedStore()
	counter := testCounting(store)

	absorbAndFlush(t, counter, servicesBatch(t, 0, 100, testReceivedAt, "checkout"))
	// A new process replays offset 100 and reads one new, undecodable record.
	replayed := servicesBatch(t, 0, 100, testReceivedAt, "checkout")
	replayed = append(replayed, &kgo.Record{
		Topic: testTopic, Partition: 0, Offset: 101, Value: []byte("not an envelope"),
	})
	absorbAndFlush(t, testCounting(store), replayed)

	if got := store.received[0]; !got.Equal(testReceivedAt) {
		t.Errorf("frontier = %s, want it held at %s", got, testReceivedAt)
	}
	if got := store.counted[0]; got != 101 {
		t.Errorf("counted through offset %d, want 101", got)
	}
}
