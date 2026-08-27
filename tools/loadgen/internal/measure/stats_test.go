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

	recorder.ObserveAnswered(Observation{Spans: 100, Status: http.StatusOK, AcceptedAt: base})
	recorder.ObserveAnswered(Observation{Spans: 500, Status: http.StatusTooManyRequests, AcceptedAt: base})

	result := recorder.Result()
	if result.SpansOffered != 600 {
		t.Errorf("SpansOffered = %d, want 600", result.SpansOffered)
	}
	if result.SpansAccepted != 100 {
		t.Errorf("SpansAccepted = %d, want 100", result.SpansAccepted)
	}
	if result.SpansRefused != 500 {
		t.Errorf("SpansRefused = %d, want 500", result.SpansRefused)
	}
	if result.Statuses[http.StatusTooManyRequests] != 1 {
		t.Errorf("throttled responses = %d, want 1", result.Statuses[http.StatusTooManyRequests])
	}
}

// Each outcome lands in exactly one bucket, and a span that went out is offered
// whatever came back. Losing an offered count is what made a correct pipeline
// look like it was inventing spans.
func TestEveryOutcomeIsAccountedForExactlyOnce(t *testing.T) {
	base := time.Unix(1000, 0)

	cases := []struct {
		name   string
		record func(*Recorder)
		want   Result
	}{
		{
			name:   "accepted",
			record: func(r *Recorder) { r.ObserveAnswered(Observation{Spans: 512, Status: http.StatusOK, AcceptedAt: base}) },
			want:   Result{Requests: 1, SpansOffered: 512, SpansAccepted: 512},
		},
		{
			name: "refused",
			record: func(r *Recorder) {
				r.ObserveAnswered(Observation{Spans: 512, Status: http.StatusServiceUnavailable, AcceptedAt: base})
			},
			want: Result{Requests: 1, SpansOffered: 512, SpansRefused: 512},
		},
		{
			name:   "unknown",
			record: func(r *Recorder) { r.ObserveUnknown(512) },
			want:   Result{Requests: 1, SpansOffered: 512, UnknownRequests: 1, UnknownSpans: 512},
		},
		{
			name:   "never sent",
			record: func(r *Recorder) { r.ObserveNotSent(512) },
			want:   Result{Requests: 1, NotSentRequests: 1, NotSentSpans: 512},
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			recorder := NewRecorder()
			c.record(recorder)
			got := recorder.Result()

			if got.Requests != c.want.Requests {
				t.Errorf("Requests = %d, want %d", got.Requests, c.want.Requests)
			}
			if got.SpansOffered != c.want.SpansOffered {
				t.Errorf("SpansOffered = %d, want %d", got.SpansOffered, c.want.SpansOffered)
			}
			if got.SpansAccepted != c.want.SpansAccepted {
				t.Errorf("SpansAccepted = %d, want %d", got.SpansAccepted, c.want.SpansAccepted)
			}
			if got.SpansRefused != c.want.SpansRefused {
				t.Errorf("SpansRefused = %d, want %d", got.SpansRefused, c.want.SpansRefused)
			}
			if got.UnknownRequests != c.want.UnknownRequests || got.UnknownSpans != c.want.UnknownSpans {
				t.Errorf("unknown = %d requests / %d spans, want %d / %d",
					got.UnknownRequests, got.UnknownSpans, c.want.UnknownRequests, c.want.UnknownSpans)
			}
			if got.NotSentRequests != c.want.NotSentRequests || got.NotSentSpans != c.want.NotSentSpans {
				t.Errorf("not sent = %d requests / %d spans, want %d / %d",
					got.NotSentRequests, got.NotSentSpans, c.want.NotSentRequests, c.want.NotSentSpans)
			}
			// Offered splits three ways and nothing else; not-sent sits outside.
			if sum := got.SpansAccepted + got.SpansRefused + got.UnknownSpans; sum != got.SpansOffered {
				t.Errorf("accepted+refused+unknown = %d, want SpansOffered %d", sum, got.SpansOffered)
			}
		})
	}
}

// An unanswered export has no round trip to time, and letting the request
// timeout in would report the client's own bound as receiver latency.
func TestUnansweredRequestsAddNoLatencySample(t *testing.T) {
	recorder := NewRecorder()
	base := time.Unix(1000, 0)

	recorder.ObserveAnswered(Observation{
		Spans: 100, Status: http.StatusOK, Latency: 5 * time.Millisecond, AcceptedAt: base,
	})
	recorder.ObserveUnknown(512)
	recorder.ObserveNotSent(512)

	result := recorder.Result()
	if result.Latency.Max != 5*time.Millisecond {
		t.Errorf("Latency.Max = %s, want 5ms: only the answered request has a latency", result.Latency.Max)
	}
	if result.AnsweredRequests() != 1 {
		t.Errorf("AnsweredRequests() = %d, want 1", result.AnsweredRequests())
	}
	if result.Requests != 3 {
		t.Errorf("Requests = %d, want 3: every request handled is counted", result.Requests)
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

func TestUnansweredRequestsAreCountedApartFromRefusals(t *testing.T) {
	recorder := NewRecorder()
	recorder.ObserveUnknown(512)

	result := recorder.Result()
	if result.Requests != 1 || result.UnknownRequests != 1 {
		t.Errorf("Requests = %d, UnknownRequests = %d, want 1 and 1",
			result.Requests, result.UnknownRequests)
	}
	if result.SpansRefused != 0 {
		t.Errorf("SpansRefused = %d, want 0: nothing was refused, the answer was lost",
			result.SpansRefused)
	}
	if len(result.Statuses) != 0 {
		t.Errorf("Statuses = %v, want none: an unanswered request has no status", result.Statuses)
	}
}
