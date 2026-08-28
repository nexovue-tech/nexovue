package derive

import (
	"testing"
	"time"
)

var receivedAt = time.Date(2026, 8, 27, 10, 30, 30, 0, time.UTC)

func skewedSpan(clientClock time.Time) Span {
	return Span{
		TenantID: "tenant-1", ProjectID: "project-1", ServiceName: "checkout",
		Endpoint: "/checkout", Timestamp: clientClock, ReceivedAt: receivedAt, DurationMS: 1,
	}
}

// The bucket a span is counted in decides when the offsets that produced it may
// commit, so a bucket has to be one that can settle. A client clock is not ours
// and cannot be trusted to produce one.
func TestATimestampOutsideTheTrustWindowIsCountedAtReceiveTime(t *testing.T) {
	receiveMinute := receivedAt.Truncate(bucketWidth)

	cases := map[string]struct {
		clientClock time.Time
		wantMinute  time.Time
		wantRetimed bool
	}{
		// A year ahead is the wedge itself: this bucket never settles, so the
		// offsets that fed it never commit and the counter map never shrinks.
		"a year ahead": {
			clientClock: receivedAt.AddDate(1, 0, 0),
			wantMinute:  receiveMinute, wantRetimed: true,
		},
		// The epoch is what an absent or out-of-range OTLP timestamp flattens
		// to. Left alone it writes a 1970 row that the metrics table's own TTL
		// deletes at the next merge — a count lost in silence.
		"the epoch": {
			clientClock: time.Unix(0, 0).UTC(),
			wantMinute:  receiveMinute, wantRetimed: true,
		},
		"beyond the broker's retention": {
			clientClock: receivedAt.Add(-trustedPastSkew - time.Minute),
			wantMinute:  receiveMinute, wantRetimed: true,
		},
		// Inside the window the client's own clock is what a dashboard should
		// show, and retiming it would move real traffic to the wrong minute.
		"a second of ordinary skew": {
			clientClock: receivedAt.Add(-time.Second),
			wantMinute:  receivedAt.Add(-time.Second).Truncate(bucketWidth),
		},
		"a little ahead of us": {
			clientClock: receivedAt.Add(trustedFutureSkew - time.Second),
			wantMinute:  receivedAt.Add(trustedFutureSkew - time.Second).Truncate(bucketWidth),
		},
		// A weekend outage replayed out of the broker must reach its real
		// minutes; that is what the broker's retention is for.
		"a replayed weekend outage": {
			clientClock: receivedAt.Add(-48 * time.Hour),
			wantMinute:  receivedAt.Add(-48 * time.Hour).Truncate(bucketWidth),
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			counters := NewCounters(0)
			minute, discarded := counters.Observe(skewedSpan(tc.clientClock))

			if !minute.Equal(tc.wantMinute) {
				t.Errorf("counted at %s, want %s", minute, tc.wantMinute)
			}
			if retimed := discarded != 0; retimed != tc.wantRetimed {
				t.Errorf("discarded skew = %s, want retimed = %v", discarded, tc.wantRetimed)
			}
			if counters.Pending() != 1 {
				t.Errorf("Pending() = %d, want the span counted exactly once", counters.Pending())
			}
		})
	}
}

// Every bucket a span can land in has to be drainable, whatever the client
// stamped on it. This is the property the offset commits depend on.
func TestEveryBucketSettlesWhateverTheClientClockSays(t *testing.T) {
	clocks := []time.Time{
		receivedAt.AddDate(1000, 0, 0),
		receivedAt.AddDate(-1000, 0, 0),
		time.Unix(0, 0).UTC(),
		receivedAt.Add(time.Hour),
		receivedAt,
	}

	counters := NewCounters(10 * time.Second)
	settled := receivedAt.Add(trustedFutureSkew + bucketWidth + time.Minute)

	for _, clock := range clocks {
		minute, _ := counters.Observe(skewedSpan(clock))
		if !counters.Drained(settled, minute) {
			t.Errorf("a span stamped %s produced a bucket (%s) still unsettled at %s: "+
				"offsets that fed it can never commit", clock, minute, settled)
		}
	}
	if counters.Drain(settled); counters.Pending() != 0 {
		t.Errorf("Pending() = %d after the drain, want the map empty", counters.Pending())
	}
}

// Without a reference clock there is nothing to judge the span against, so it
// is counted as stamped. The pipeline always supplies one; this documents what
// the type alone does not.
func TestWithoutAReceiveTimeTheSpanTimestampIsUsedAsIs(t *testing.T) {
	counters := NewCounters(0)
	span := skewedSpan(receivedAt.AddDate(1, 0, 0))
	span.ReceivedAt = time.Time{}

	minute, discarded := counters.Observe(span)
	if !minute.Equal(span.Timestamp.Truncate(bucketWidth)) {
		t.Errorf("counted at %s, want the span's own minute %s",
			minute, span.Timestamp.Truncate(bucketWidth))
	}
	if discarded != 0 {
		t.Errorf("discarded = %s, want nothing reported when there was nothing to compare", discarded)
	}
}
