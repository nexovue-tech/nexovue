package serviceedges

import (
	"reflect"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/clocktrust"
)

func TestOptionsMustBoundTheAccumulator(t *testing.T) {
	valid := testOptions()
	for name, options := range map[string]Options{
		"no pair window": {PairWindow: 0, LateGrace: valid.LateGrace, MaxPending: valid.MaxPending},
		"negative grace": {PairWindow: valid.PairWindow, LateGrace: -time.Second, MaxPending: valid.MaxPending},
		"no bound":       {PairWindow: valid.PairWindow, LateGrace: valid.LateGrace, MaxPending: 0},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := New(options); err == nil {
				t.Fatal("expected the options to be rejected")
			}
		})
	}
}

// A timestamp we can trust decides the bucket, because that is the minute the
// call actually happened in.
func TestATrustedTimestampDecidesTheMinute(t *testing.T) {
	accumulator := newTestAccumulator(t)
	client := testSpan(KindClient, "checkout", 0x01, 0)
	client.Timestamp = baseTime.Add(-30 * time.Second) // the previous minute

	minute, discarded := accumulator.Observe(baseTime, client)
	if discarded != 0 {
		t.Fatalf("expected no discarded skew, got %s", discarded)
	}

	edges := accumulator.Drain(settledTime)
	if len(edges) != 1 {
		t.Fatalf("expected one edge, got %+v", edges)
	}
	if want := startOfMinute(client.Timestamp); edges[0].Minute != want {
		t.Fatalf("expected the edge in %s, got %s", want, edges[0].Minute)
	}
	// The settlement marker is never behind the minute we are processing in, so
	// the offset waits for the pairing as well as for the row.
	if minute != startOfMinute(baseTime) {
		t.Fatalf("expected the marker at the processing minute, got %s", minute)
	}
}

// A client clock too far ahead of our own is not usable, so the call is
// attributed to when we received it and the skew is reported.
func TestAnUntrustedTimestampCountsAtReceiveTime(t *testing.T) {
	accumulator := newTestAccumulator(t)
	client := testSpan(KindClient, "checkout", 0x01, 0)
	client.Timestamp = baseTime.Add(90 * time.Minute)

	_, discarded := accumulator.Observe(baseTime, client)
	if discarded != 90*time.Minute {
		t.Fatalf("expected the skew reported, got %s", discarded)
	}

	edges := accumulator.Drain(settledTime)
	if len(edges) != 1 || edges[0].Minute != startOfMinute(baseTime) {
		t.Fatalf("expected the edge at receive time, got %+v", edges)
	}
}

// A span with no receive time is judged against the clock we are processing on.
// Leaving it unjudged would let a wrong client clock open a bucket that never
// settles, which stops offsets committing for good.
func TestASpanWithNoReceiveTimeIsJudgedAgainstOurOwnClock(t *testing.T) {
	accumulator := newTestAccumulator(t)
	client := testSpan(KindClient, "checkout", 0x01, 0)
	client.ReceivedAt = time.Time{}
	client.Timestamp = baseTime.Add(365 * 24 * time.Hour)

	minute, discarded := accumulator.Observe(baseTime, client)
	if discarded == 0 {
		t.Fatal("expected the year-ahead timestamp to be rejected")
	}
	if minute != startOfMinute(baseTime) {
		t.Fatalf("expected the marker at the processing minute, got %s", minute)
	}
}

// Replaying a week-old outage into its real minutes is deliberate, so an old
// timestamp still decides the bucket — but the marker must not, or the offset
// would commit while the span was still waiting for its partner.
func TestAReplayedSpanKeepsItsOwnMinuteButNotItsOwnMarker(t *testing.T) {
	accumulator := newTestAccumulator(t)
	replayedAt := baseTime.Add(-6 * 24 * time.Hour)
	client := testSpan(KindClient, "checkout", 0x01, 0)
	client.Timestamp, client.ReceivedAt = replayedAt, replayedAt

	minute, discarded := accumulator.Observe(baseTime, client)
	if discarded != 0 {
		t.Fatalf("a replay within the broker's retention is trusted, got skew %s", discarded)
	}
	if minute != startOfMinute(baseTime) {
		t.Fatalf("expected the marker at the processing minute, got %s", minute)
	}
	if accumulator.Drained(baseTime, minute) {
		t.Fatal("the span is still waiting to pair; its offset must not be committable")
	}

	edges := accumulator.Drain(settledTime)
	if len(edges) != 1 || edges[0].Minute != startOfMinute(replayedAt) {
		t.Fatalf("expected the edge in its own week-old minute, got %+v", edges)
	}
}

// Whatever a client's clock says, the bucket a span is put in has to settle, or
// the offsets behind it never commit.
func TestEveryMarkerSettlesWithinABoundedTime(t *testing.T) {
	options := testOptions()
	horizon := clocktrust.Bucket + clocktrust.FutureSkew + options.PairWindow + options.LateGrace + time.Second

	for name, timestamp := range map[string]time.Time{
		"far future":   baseTime.Add(1000 * time.Hour),
		"just ahead":   baseTime.Add(clocktrust.FutureSkew - time.Second),
		"far past":     baseTime.Add(-1000 * time.Hour),
		"within retry": baseTime.Add(-6 * 24 * time.Hour),
		"at now":       baseTime,
	} {
		t.Run(name, func(t *testing.T) {
			accumulator := newTestAccumulator(t)
			client := testSpan(KindClient, "checkout", 0x01, 0)
			client.Timestamp = timestamp

			minute, _ := accumulator.Observe(baseTime, client)
			if !accumulator.Drained(baseTime.Add(horizon), minute) {
				t.Fatalf("marker %s had not settled %s after the span arrived", minute, horizon)
			}
		})
	}
}

func TestAMinuteIsNotDrainedWhileASpanCouldStillJoinIt(t *testing.T) {
	accumulator := newTestAccumulator(t)
	minute, _ := accumulator.Observe(baseTime, testSpan(KindClient, "checkout", 0x01, 0))

	options := testOptions()
	// The minute has ended and its grace has run out, but a span that arrived at
	// the end of it may still be waiting for a partner.
	tooEarly := startOfMinute(baseTime).Add(clocktrust.Bucket + options.LateGrace)
	if accumulator.Drained(tooEarly, minute) {
		t.Fatal("the minute was declared drained before the pairing window closed")
	}
	if edges := accumulator.Drain(tooEarly); len(edges) != 0 {
		t.Fatalf("the minute was written before the pairing window closed: %+v", edges)
	}

	justEnough := tooEarly.Add(options.PairWindow)
	if !accumulator.Drained(justEnough, minute) {
		t.Fatal("the minute never settled")
	}
	if edges := accumulator.Drain(justEnough); len(edges) != 1 {
		t.Fatalf("expected the minute to be written, got %+v", edges)
	}
}

func TestAZeroMinuteIsTriviallyDrained(t *testing.T) {
	if !newTestAccumulator(t).Drained(baseTime, time.Time{}) {
		t.Fatal("a span that fed no bucket must not hold an offset")
	}
}

func TestCallsToTheSameServicesShareOneRow(t *testing.T) {
	accumulator := newTestAccumulator(t)
	for id := byte(1); id <= 6; id += 2 {
		client := testSpan(KindClient, "checkout", id, 0)
		client.DurationMS = float64(id)
		server := testSpan(KindServer, "payments", id+1, id)
		server.Errored = id == 3
		accumulator.Observe(baseTime, client)
		accumulator.Observe(baseTime, server)
	}

	edges := accumulator.Drain(settledTime)
	if len(edges) != 1 {
		t.Fatalf("expected one row, got %+v", edges)
	}
	if edges[0].CallCount != 3 || edges[0].ErrorCount != 1 {
		t.Fatalf("expected 3 calls and 1 error, got %d and %d", edges[0].CallCount, edges[0].ErrorCount)
	}
	if want := []float64{1, 3, 5}; !reflect.DeepEqual(edges[0].LatencyMS, want) {
		t.Fatalf("expected client latencies %v, got %v", want, edges[0].LatencyMS)
	}
}

// A restart re-derives the same minute from the same offsets, and the store only
// recognises the repeat if the block is byte-identical (ADR 0060). Go randomises
// map iteration, so this is the guard on the sort.
func TestTheSameSpansAlwaysDrainToTheSameRows(t *testing.T) {
	build := func() []Edge {
		accumulator := newTestAccumulator(t)
		for id := byte(1); id <= 40; id += 2 {
			client := testSpan(KindClient, "checkout", id, 0)
			client.ServiceName = []string{"checkout", "cart", "search"}[int(id)%3]
			client.DurationMS = float64(id)
			server := testSpan(KindServer, "payments", id+1, id)
			server.ServiceName = []string{"payments", "ledger"}[int(id)%2]
			accumulator.Observe(baseTime, client)
			accumulator.Observe(baseTime, server)
		}
		return accumulator.Drain(settledTime)
	}

	first := build()
	if len(first) < 2 {
		t.Fatalf("expected several rows to order, got %d", len(first))
	}
	for attempt := 0; attempt < 25; attempt++ {
		if again := build(); !reflect.DeepEqual(first, again) {
			t.Fatalf("drain %d differed:\n%+v\nvs\n%+v", attempt, first, again)
		}
	}
}

func TestDrainLeavesOpenMinutesAlone(t *testing.T) {
	accumulator := newTestAccumulator(t)
	accumulator.Observe(baseTime, testSpan(KindClient, "checkout", 0x01, 0))
	accumulator.Observe(baseTime, testSpan(KindServer, "payments", 0x02, 0x01))

	if edges := accumulator.Drain(baseTime); len(edges) != 0 {
		t.Fatalf("expected the open minute to be left alone, got %+v", edges)
	}
	if got := accumulator.Stats().OpenEdges; got != 1 {
		t.Fatalf("expected the edge to still be held, got %d", got)
	}
	if edges := accumulator.Drain(settledTime); len(edges) != 1 {
		t.Fatalf("expected the closed minute to be written, got %+v", edges)
	}
	if got := accumulator.Stats().OpenEdges; got != 0 {
		t.Fatalf("expected the bucket to be released, got %d", got)
	}
}
