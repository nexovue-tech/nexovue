//go:build chaos

package chaos

import (
	"fmt"
	"strconv"
	"strings"
	"testing"
	"time"
)

// Counter idempotency, proven deterministically rather than raced.
//
// The first design made a replayed counter block harmless by opening
// ClickHouse's insert deduplication window, and recorded that the log might not
// survive a restart of ClickHouse itself. Measurement confirmed it does not.
// The durable per-partition state in KeeperMap is what replaced it.
//
// The chaos test cannot prove this: whether a replay even happens there depends on
// where the outage lands, and its overcount signal is confounded by the receiver
// republishing unacknowledged exports. So this test creates the condition instead
// of waiting for it — drain fully, wipe the deduplication log by restarting the
// store, then rewind the consumer group behind the durable watermark and demand
// the counters do not move.
func TestReplayedOffsetsAreNotCountedTwice(t *testing.T) {
	const rewindPerPartition = 800

	requireHealthy(t)
	t.Cleanup(func() { restoreStack(t) })
	quietenDemo(t)

	// A rewind is only meaningful once everything ahead of it is settled.
	waitForQuiet(t)

	before := totalCounted(t)
	if before == 0 {
		t.Skip("no counters recorded yet; run `just chaos` or the demo app first")
	}
	t.Logf("counters before: %d", before)

	// Restarting the store is what makes this a real test of the durable state:
	// it discards the in-memory deduplication log that would otherwise catch a
	// replayed block and hide whether the state machine works at all.
	t.Log("restarting clickhouse (which does NOT discard its deduplication log — measured; the rewind below is what removes that protection)")
	compose(t, "restart", "clickhouse")
	waitForStore(t)

	// The worker must not hold partitions while offsets are rewritten, and Kafka
	// only notices a departed member after the session timeout — stopping the
	// container is not enough on its own.
	t.Log("stopping the worker so the group can be rewound")
	compose(t, "stop", "worker")
	waitForGroupInactive(t)

	out := compose(t, "exec", "-T", "kafka", "/opt/kafka/bin/kafka-consumer-groups.sh",
		"--bootstrap-server", "kafka:9092", "--group", spansGroup,
		"--topic", spansTopic, "--reset-offsets", "--shift-by",
		"-"+strconv.Itoa(rewindPerPartition), "--execute")
	if strings.Contains(strings.ToLower(out), "error") {
		t.Fatalf("could not rewind the consumer group:\n%s", out)
	}
	t.Logf("rewound %d offsets per partition — those records were already counted", rewindPerPartition)

	compose(t, "up", "-d", "--wait", "worker")
	waitForQuiet(t)

	after := totalCounted(t)
	switch {
	case after > before:
		t.Errorf("REPLAY WAS COUNTED TWICE: %d before, %d after re-consuming %d offsets "+
			"per partition (%d extra). The durable ingest state did not stop it",
			before, after, rewindPerPartition, after-before)
	case after < before:
		t.Errorf("counters went backwards: %d before, %d after — a replay must never "+
			"remove counts", before, after)
	default:
		t.Logf("replay ignored: %d counters before and after, with ClickHouse's "+
			"deduplication log discarded in between", after)
	}
}

// waitForQuiet blocks until the consumer group has caught up and the counters
// have stopped moving, so a measurement is not taken mid-flight.
func waitForQuiet(t *testing.T) {
	t.Helper()
	deadline := time.Now().Add(4 * time.Minute)
	stable, previous := 0, -1

	for time.Now().Before(deadline) {
		time.Sleep(5 * time.Second)
		if consumerLag(t, spansGroup, spansTopic) > 0 {
			stable, previous = 0, -1
			continue
		}
		counted := totalCounted(t)
		if counted == previous {
			if stable++; stable >= 3 {
				return
			}
			continue
		}
		stable, previous = 0, counted
	}
	t.Log("gave up waiting for the pipeline to settle; measuring anyway")
}

// waitForGroupInactive blocks until Kafka reports the consumer group as having
// no members, which is the only state in which offsets may be rewritten.
func waitForGroupInactive(t *testing.T) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Minute)
	for time.Now().Before(deadline) {
		out := compose(t, "exec", "-T", "kafka", "/opt/kafka/bin/kafka-consumer-groups.sh",
			"--bootstrap-server", "kafka:9092", "--group", spansGroup,
			"--describe", "--state")
		if strings.Contains(out, "Empty") || strings.Contains(out, "Dead") {
			t.Log("consumer group is inactive")
			return
		}
		time.Sleep(5 * time.Second)
	}
	t.Fatal("the consumer group never went inactive; offsets cannot be rewound")
}

// waitForStore blocks until ClickHouse answers again.
func waitForStore(t *testing.T) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Minute)
	for time.Now().Before(deadline) {
		if query(t, "SELECT 1") == "1" {
			return
		}
		time.Sleep(2 * time.Second)
	}
	t.Fatal("clickhouse did not come back")
}

// totalCounted is every exact span counter recorded, across all services.
func totalCounted(t *testing.T) int {
	t.Helper()
	answer := query(t, fmt.Sprintf(
		"SELECT toUInt64(sum(value)) FROM metrics_sum WHERE metric_name = '%s'", spanCountMetric))
	if answer == "" {
		return 0
	}
	counted, err := strconv.Atoi(answer)
	if err != nil {
		t.Fatalf("unreadable counter total %q: %v", answer, err)
	}
	return counted
}

const spanCountMetric = "nexovue.spans.count"
