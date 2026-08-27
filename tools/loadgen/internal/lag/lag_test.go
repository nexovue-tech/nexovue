package lag

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestTrackerResolvesAFollowedTrace(t *testing.T) {
	const traceID = "0123456789abcdef0123456789abcdef"

	storage := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, traceID+"\n")
	}))
	defer storage.Close()

	tracker := newTestTracker(storage.URL)
	tracker.Follow(traceID, time.Now().Add(-2*time.Second))

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	tracker.Run(ctx)

	result := tracker.Result()
	if result.Samples != 1 {
		t.Fatalf("Samples = %d, want 1", result.Samples)
	}
	if result.Lost != 0 {
		t.Errorf("Lost = %d, want 0", result.Lost)
	}
	if result.P50 < 2*time.Second {
		t.Errorf("P50 = %s, want at least the 2s the trace waited", result.P50)
	}
}

// A trace that never arrives is a lost measurement. Counting it as a lag of
// zero would quietly improve the percentiles the budget is judged on.
func TestTrackerCountsTracesThatNeverArrive(t *testing.T) {
	storage := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "")
	}))
	defer storage.Close()

	tracker := newTestTracker(storage.URL)
	tracker.Follow("0123456789abcdef0123456789abcdef", time.Now())

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	tracker.Run(ctx)

	result := tracker.Result()
	if result.Samples != 0 {
		t.Errorf("Samples = %d, want 0", result.Samples)
	}
	if result.Lost != 1 {
		t.Errorf("Lost = %d, want 1", result.Lost)
	}
}

func TestTrackerReportsAnUnreachableStore(t *testing.T) {
	storage := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "table nexovue.spans does not exist", http.StatusInternalServerError)
	}))
	defer storage.Close()

	tracker := newTestTracker(storage.URL)
	tracker.Follow("0123456789abcdef0123456789abcdef", time.Now())

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	tracker.Run(ctx)

	result := tracker.Result()
	if result.Failure == nil {
		t.Fatal("Failure is nil, want the reason nothing could be measured")
	}
	if !strings.Contains(result.Failure.Error(), "500") {
		t.Errorf("Failure = %v, want it to carry the store's answer", result.Failure)
	}
}

func TestTrackerIgnoresARepeatedTrace(t *testing.T) {
	tracker := newTestTracker("http://127.0.0.1:1")
	accepted := time.Now()
	tracker.Follow("aa", accepted)
	tracker.Follow("aa", accepted.Add(time.Second))

	if got := len(tracker.outstanding()); got != 1 {
		t.Errorf("outstanding = %d, want 1", got)
	}
}

func TestQuoteEscapesSoAValueCannotEndTheLiteral(t *testing.T) {
	if got := quote("o'brien"); got != `'o\'brien'` {
		t.Errorf("quote = %s, want the quote escaped", got)
	}
}

func newTestTracker(url string) *Tracker {
	return NewTracker(Options{
		URL:          url,
		User:         "test",
		Password:     "test",
		Database:     "nexovue",
		ServiceName:  "loadgen",
		PollInterval: 50 * time.Millisecond,
	})
}
