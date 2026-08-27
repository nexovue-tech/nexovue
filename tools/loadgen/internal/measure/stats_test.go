package measure

import (
	"net/http"
	"testing"
	"time"
)

func TestPercentileUsesNearestRank(t *testing.T) {
	sorted := []time.Duration{
		1 * time.Millisecond, 2 * time.Millisecond, 3 * time.Millisecond,
		4 * time.Millisecond, 100 * time.Millisecond,
	}

	cases := []struct {
		share float64
		want  time.Duration
	}{
		{0.50, 3 * time.Millisecond},
		{0.90, 100 * time.Millisecond},
		{0.99, 100 * time.Millisecond},
	}
	for _, c := range cases {
		if got := percentile(sorted, c.share); got != c.want {
			t.Errorf("percentile(%.2f) = %s, want %s", c.share, got, c.want)
		}
	}
}

func TestPercentileReturnsAnObservedSample(t *testing.T) {
	sorted := []time.Duration{10 * time.Millisecond, 20 * time.Millisecond}
	for _, share := range []float64{0, 0.5, 0.99, 1} {
		got := percentile(sorted, share)
		if got != sorted[0] && got != sorted[1] {
			t.Errorf("percentile(%.2f) = %s, which is not a sample that was taken", share, got)
		}
	}
}

// Rejected spans must not count towards throughput: reporting refused load as
// accepted load is the exact failure the budget table is meant to catch.
func TestOnlyAcceptedSpansCountTowardsThroughput(t *testing.T) {
	recorder := NewRecorder()
	base := time.Unix(1000, 0)

	recorder.Observe(Observation{Spans: 100, Status: http.StatusOK, AcceptedAt: base})
	recorder.Observe(Observation{Spans: 500, Status: http.StatusTooManyRequests, AcceptedAt: base})

	result := recorder.Result()
	if result.SpansOffered != 600 {
		t.Errorf("SpansOffered = %d, want 600", result.SpansOffered)
	}
	if result.SpansAccepted != 100 {
		t.Errorf("SpansAccepted = %d, want 100", result.SpansAccepted)
	}
	if result.Statuses[http.StatusTooManyRequests] != 1 {
		t.Errorf("throttled responses = %d, want 1", result.Statuses[http.StatusTooManyRequests])
	}
}

func TestThroughputDropsPartialEndSeconds(t *testing.T) {
	// The first and last second are partial; only the middle three are whole.
	perSecond := map[int64]int{
		100: 40, // partial start
		101: 900,
		102: 1000,
		103: 1100,
		104: 30, // partial end
	}

	got := throughput(perSecond)
	if got.Seconds != 3 {
		t.Fatalf("Seconds = %d, want 3", got.Seconds)
	}
	if got.Sustained != 1000 {
		t.Errorf("Sustained = %.0f, want 1000", got.Sustained)
	}
	if got.Peak != 1100 {
		t.Errorf("Peak = %.0f, want 1100", got.Peak)
	}
}

func TestThroughputNeedsMoreThanTwoSeconds(t *testing.T) {
	got := throughput(map[int64]int{100: 500, 101: 500})
	if got.Seconds != 0 {
		t.Errorf("Seconds = %d, want 0 for a run with no whole second", got.Seconds)
	}
}

func TestFailuresAreCountedApartFromRefusals(t *testing.T) {
	recorder := NewRecorder()
	recorder.ObserveFailure()

	result := recorder.Result()
	if result.Requests != 1 || result.TransportFailures != 1 {
		t.Errorf("Requests = %d, TransportFailures = %d, want 1 and 1",
			result.Requests, result.TransportFailures)
	}
	if len(result.Statuses) != 0 {
		t.Errorf("Statuses = %v, want none: an unanswered request has no status", result.Statuses)
	}
}
