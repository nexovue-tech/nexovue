//go:build chaos

package chaos

import (
	"context"
	"fmt"
	"sync"
	"testing"
	"time"
)

// Invariant 2 for the two signals the span test cannot speak for.
//
// Logs and metric points travel the same broker and the same commit discipline
// as spans, but neither is sampled and neither is counted — the rows themselves
// are the evidence. That makes a stronger assertion available here than the span
// test can make: every acknowledged record must be *present*, not merely
// counted, because nothing downstream is entitled to drop one.
//
// So each generated record carries its own identity — a log's body, a metric
// point's single attribute — and the check is on distinct identities in the
// store. Duplicates are expected and say nothing: a redelivered batch writes its
// rows again, and neither pipeline deduplicates. Only a shortfall is loss.
//
// What it deliberately does not assert:
//
//   - that the stored row count equals the acknowledged count. At-least-once
//     redelivery makes duplicates correct, and for metrics the five inserts are
//     not one transaction, so a failure partway through leaves the earlier
//     tables holding rows the redelivery writes again.
//   - that the five metric tables agree with each other. A partial failure is
//     designed for; each table is asked separately whether *it* lost anything.
//   - anything about ordering, timing, or which outage a record survived.
const (
	signalLoadDuration = 100 * time.Second
	killKafkaAtSignal  = 25 * time.Second
	killStoreAtSignal  = 65 * time.Second

	// Nothing is buffered on these two paths — a record is written where it is
	// decoded — so the only wait after the load is the consumer catching up.
	// The budget is for the backlog the two outages built, not for a window.
	signalDrainTimeout = 3 * time.Minute

	// Four log workers on a 200ms tick is 500 records a second; two metric
	// workers on a 250ms tick is 40 identities a second in each of the five
	// tables. Both sit far below the receiver's 1,000 req/s limit and keep the
	// pipeline busy across both outages without saturating the laptop.
	logExportWorkers   = 4
	logExportInterval  = 200 * time.Millisecond
	metricExportWorker = 2
	metricInterval     = 250 * time.Millisecond
)

// The topics and groups the two pipelines read, from
// apps/workers/internal/config. Metrics have a group of their own; logs share
// the span group, so the log topic has to be named to read its lag alone.
const (
	logsTopic    = "logs-raw"
	logsDLQTopic = "logs-dlq"
	// Its own group since ADR 0075's sibling change: logs used to share the span
	// group, and a lag read against that one now never drains, because those
	// offsets are frozen where the pipeline left them.
	logsGroup       = "nexovue-workers-logs"
	metricsTopic    = "metrics-raw"
	metricsDLQTopic = "metrics-dlq"
	metricsGroup    = "nexovue-workers-metrics"
)

func TestNoAcknowledgedLogOrMetricIsEverLost(t *testing.T) {
	service := fmt.Sprintf("chaos-signals-%d", time.Now().UnixNano())
	t.Logf("service under test: %s", service)

	requireHealthy(t)
	t.Cleanup(func() { restoreStack(t) })
	quietenDemo(t)

	// Nothing drains a dead-letter topic by design, so only the delta is ours.
	logsParkedBefore := dlqDepth(t, logsDLQTopic)
	metricsParkedBefore := dlqDepth(t, metricsDLQTopic)

	loadCtx, endLoad := context.WithTimeout(context.Background(), signalLoadDuration)
	defer endLoad()

	var logs, metrics *tally
	var loading sync.WaitGroup
	loading.Add(2)
	go func() {
		defer loading.Done()
		logs = logsDoor.drive(loadCtx, t, service, logExportWorkers, logExportInterval)
	}()
	go func() {
		defer loading.Done()
		metrics = metricsDoor.drive(loadCtx, t, service, metricExportWorker, metricInterval)
	}()

	// Both outages are timed from the start of the run rather than chained, so a
	// slow restart cannot push the second failure past the end of the load.
	sleep(t, killKafkaAtSignal)
	outage(t, "kafka")
	sleep(t, killStoreAtSignal-killKafkaAtSignal-outageLength)
	outage(t, "clickhouse")
	loading.Wait()

	t.Logf("logs: %s", logs.describe())
	t.Logf("metric points per shape: %s", metrics.describe())
	if logs.accepted() == 0 {
		t.Fatal("the receiver accepted no logs; the run proves nothing about loss")
	}
	if metrics.accepted() == 0 {
		t.Fatal("the receiver accepted no metrics; the run proves nothing about loss")
	}

	waitForSignalDrain(t, logsGroup, logsTopic)
	waitForSignalDrain(t, metricsGroup, metricsTopic)

	t.Run("logs", func(t *testing.T) {
		stored, distinct := countStored(t, "logs", "body", service)
		assertNothingLost(t, "logs", logs, stored, distinct)
	})
	for _, shape := range metricShapes {
		t.Run(shape.table, func(t *testing.T) {
			stored, distinct := countStored(t, shape.table, "toJSONString(attrs)", service)
			assertNothingLost(t, shape.table, metrics, stored, distinct)
		})
	}

	t.Run("dead-letter", func(t *testing.T) {
		assertNothingParked(t, logsDLQTopic, logsParkedBefore)
		assertNothingParked(t, metricsDLQTopic, metricsParkedBefore)
	})
}

// assertNothingLost is the two-sided check an unsampled signal allows.
//
// Below the acknowledged count is loss, and loss is what this test exists to
// forbid: the receiver said it had taken those records, and nothing between it
// and the store is entitled to drop one.
//
// Above it is not a bug — redelivery writes the same rows again — but it is
// still bounded: a record can only be in the store if the generator offered it,
// so more distinct identities than were ever offered would mean the query is
// reading someone else's rows and the whole measurement is worthless.
func assertNothingLost(t *testing.T, table string, offered *tally, stored, distinct int) {
	t.Helper()

	if taken := offered.accepted(); distinct < taken {
		t.Errorf("DATA LOSS in %s: the receiver acknowledged %d records, only %d are "+
			"present (%d missing)", table, taken, distinct, taken-distinct)
	}
	if ceiling := offered.offered(); distinct > ceiling {
		t.Errorf("%s holds %d distinct records but only %d were ever offered; the query "+
			"is counting rows this run did not produce", table, distinct, ceiling)
	}
	t.Logf("%s: %d acknowledged, %d present, %d rows (%d duplicates, which at-least-once "+
		"delivery permits)", table, offered.accepted(), distinct, stored, stored-distinct)
}

func assertNothingParked(t *testing.T, topic string, before int) {
	t.Helper()
	if parked := dlqDepth(t, topic) - before; parked > 0 {
		t.Errorf("%d messages were parked on %s during the run; an outage must not make "+
			"a well-formed envelope undecodable", parked, topic)
	}
}

// waitForSignalDrain waits until one consumer group has committed everything on
// its topic.
//
// That is all "caught up" means for these two signals, and it is the difference
// from the span test. Trace assembly holds a trace for the sampling window and
// its counters for the minute bucket, so the span test has to wait again after
// the lag reaches zero. Here a record is written where it is decoded and the
// offset only moves once the insert has returned, so a committed offset at the
// end of the log already means the rows are in ClickHouse.
func waitForSignalDrain(t *testing.T, group, topic string) {
	t.Helper()
	deadline := time.Now().Add(signalDrainTimeout)

	for time.Now().Before(deadline) {
		lag := consumerLag(t, group, topic)
		if lag == 0 {
			t.Logf("%s caught up on %s", group, topic)
			return
		}
		t.Logf("draining %s… lag %d", topic, lag)
		time.Sleep(drainPoll)
	}

	t.Errorf("PIPELINE STALLED: %s still has %d unconsumed messages after %s. Nothing was "+
		"lost — the worker stopped fetching. Check whether it wedged on a call with no "+
		"deadline rather than treating this as loss",
		topic, consumerLag(t, group, topic), signalDrainTimeout)
}

// countStored returns how many rows one table holds for this run and how many
// distinct records they represent. The two differ by the duplicates redelivery
// wrote, which is a fact worth logging and not a failure.
func countStored(t *testing.T, table, identity, service string) (stored, distinct int) {
	t.Helper()
	answer := query(t, fmt.Sprintf(
		"SELECT count(), uniqExact(%s) FROM %s WHERE service_name = '%s'",
		identity, table, service))
	if answer == "" {
		return 0, 0
	}
	var rows, unique int
	if _, err := fmt.Sscanf(answer, "%d\t%d", &rows, &unique); err != nil {
		t.Fatalf("unreadable count %q from %s: %v", answer, table, err)
	}
	return rows, unique
}
