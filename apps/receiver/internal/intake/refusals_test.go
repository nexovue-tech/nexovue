package intake

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

const testReceiver = "receiver-a"

// writtenMinute is one upsert as the control plane would receive it.
type writtenMinute struct {
	minute     time.Time
	receiverID string
	reason     string
	count      int
}

// recordingWriter stands in for Postgres, failing while `down` is set.
type recordingWriter struct {
	mu      sync.Mutex
	written []writtenMinute
	down    bool
}

func (w *recordingWriter) RecordRefusals(
	_ context.Context, minute time.Time, receiverID, reason string, count int,
) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.down {
		return errors.New("dial tcp: connection refused")
	}
	w.written = append(w.written, writtenMinute{minute, receiverID, reason, count})
	return nil
}

func (w *recordingWriter) all() []writtenMinute {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([]writtenMinute(nil), w.written...)
}

var noon = time.Date(2026, 11, 1, 12, 0, 0, 0, time.UTC)

func refuse(recorder *Recorder, at time.Time, times int) {
	for range times {
		recorder.Refused(at)
	}
}

func TestAMinuteWithEnoughRefusalsIsWrittenOnceItEnds(t *testing.T) {
	writer := &recordingWriter{}
	recorder := NewRecorder(writer, testReceiver)
	refuse(recorder, noon.Add(10*time.Second), MinRefusalsPerMinute+2)

	recorder.flush(context.Background(), noon.Add(50*time.Second))
	if got := writer.all(); len(got) != 0 {
		t.Fatalf("wrote %v while the minute was still running; its count is not final", got)
	}

	recorder.flush(context.Background(), noon.Add(time.Minute+time.Second))
	recorder.flush(context.Background(), noon.Add(2*time.Minute))

	want := writtenMinute{noon, testReceiver, ReasonBrokerUnavailable, MinRefusalsPerMinute + 2}
	if got := writer.all(); len(got) != 1 || got[0] != want {
		t.Fatalf("written = %v, want exactly %v: one upsert per minute per receiver", got, want)
	}
}

// Design 2.1, item 1: a few refusals in a minute are not an outage, and must not
// hold every project's minute.
func TestAMinuteWithFewerRefusalsThanTheThresholdIsNotWritten(t *testing.T) {
	writer := &recordingWriter{}
	recorder := NewRecorder(writer, testReceiver)
	refuse(recorder, noon.Add(5*time.Second), MinRefusalsPerMinute-1)

	recorder.flush(context.Background(), noon.Add(time.Minute+time.Second))

	if got := writer.all(); len(got) != 0 {
		t.Fatalf("written = %v, want nothing below %d refusals", got, MinRefusalsPerMinute)
	}
}

func TestRefusalsAreCountedInTheMinuteTheyHappened(t *testing.T) {
	writer := &recordingWriter{}
	recorder := NewRecorder(writer, testReceiver)
	refuse(recorder, noon.Add(59*time.Second), MinRefusalsPerMinute)
	refuse(recorder, noon.Add(time.Minute), 1)

	recorder.flush(context.Background(), noon.Add(2*time.Minute+time.Second))

	got := writer.all()
	if len(got) != 1 || !got[0].minute.Equal(noon) || got[0].count != MinRefusalsPerMinute {
		t.Fatalf("written = %v, want only 12:00 with %d", got, MinRefusalsPerMinute)
	}
}

// The control plane may be the store that is down. A minute waits for it, but not
// past the scheduler's hold limit, after which it would hold nothing.
func TestAFailedWriteIsRetriedUntilTheMinuteCanNoLongerHoldAnything(t *testing.T) {
	writer := &recordingWriter{down: true}
	recorder := NewRecorder(writer, testReceiver)
	refuse(recorder, noon, MinRefusalsPerMinute)
	refuse(recorder, noon.Add(time.Minute), MinRefusalsPerMinute)

	recorder.flush(context.Background(), noon.Add(2*time.Minute))
	writer.mu.Lock()
	writer.down = false
	writer.mu.Unlock()
	recorder.flush(context.Background(), noon.Add(keptUnwritten+time.Minute+time.Second))

	got := writer.all()
	if len(got) != 1 || !got[0].minute.Equal(noon.Add(time.Minute)) {
		t.Fatalf("written = %v, want only 12:01: 12:00 is past the hold limit", got)
	}
	recorder.flush(context.Background(), noon.Add(keptUnwritten+2*time.Minute))
	if again := writer.all(); len(again) != 1 {
		t.Fatalf("written = %v, want a written minute forgotten", again)
	}
}

func TestRunWritesOnItsOwnAndStopsWithItsContext(t *testing.T) {
	writer := &recordingWriter{}
	recorder := NewRecorder(writer, testReceiver)
	recorder.flushEvery = 5 * time.Millisecond
	refuse(recorder, time.Now().Add(-time.Minute), MinRefusalsPerMinute)

	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		recorder.Run(ctx)
	}()

	deadline := time.Now().Add(2 * time.Second)
	for len(writer.all()) == 0 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	cancel()
	select {
	case <-stopped:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return after its context ended")
	}
	if len(writer.all()) != 1 {
		t.Fatalf("written = %v, want the finished minute written by Run", writer.all())
	}
}
