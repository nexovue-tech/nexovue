package sample_test

import (
	"slices"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// factsOn is one span's facts with the partition its record arrived on.
func factsOn(partition int32, traceID string) sample.Facts {
	held := facts(traceID, 0, true)
	held.Partition = partition
	return held
}

// The defect this closes: a partition moving between two live workers had every
// span still in the losing worker's buffer written twice — once by the worker
// that was told nothing, and once by the worker that read the same uncommitted
// records from the broker.
func TestDisownedPartitionsSpansAreNeverReleased(t *testing.T) {
	buffer := newBuffer(t, keepEverythingSlow(), defaultOptions())

	buffer.Add(base, factsOn(3, "trace-leaving"), "leaving-1")
	buffer.Add(base, factsOn(3, "trace-leaving"), "leaving-2")
	buffer.Add(base, factsOn(5, "trace-staying"), "staying-1")

	dropped := buffer.Disown([]int32{3})
	if dropped.Spans != 2 || dropped.Traces != 1 {
		t.Fatalf("Disown() = %+v, want 2 spans and 1 trace", dropped)
	}

	released := buffer.Expire(base.Add(time.Minute))
	if !slices.Equal(released, []string{"staying-1"}) {
		t.Errorf("Expire() = %v, want only the partition this worker still owns; "+
			"every other row here is one the new owner writes as well", released)
	}

	stats := buffer.Stats()
	if stats.SpansDisowned != 2 || stats.TracesDisowned != 1 {
		t.Errorf("Stats() disowned = %d spans / %d traces, want 2 and 1",
			stats.SpansDisowned, stats.TracesDisowned)
	}
	if stats.BufferedSpans != 0 || stats.OpenTraces != 0 {
		t.Errorf("Stats() still holds %d spans in %d traces after everything expired",
			stats.BufferedSpans, stats.OpenTraces)
	}
}

// The receiver keys spans by trace id, so a trace is normally one partition's
// whole world — but nothing in the buffer may rest on it. A trace that did
// arrive on two partitions must lose only the half that left, or the surviving
// partition's offsets would be committed over rows nothing wrote.
func TestATraceSpanningTwoPartitionsLosesOnlyTheHalfThatLeft(t *testing.T) {
	buffer := newBuffer(t, keepEverythingSlow(), defaultOptions())

	buffer.Add(base, factsOn(3, "trace-split"), "from-3")
	buffer.Add(base, factsOn(5, "trace-split"), "from-5")

	if dropped := buffer.Disown([]int32{3}); dropped.Spans != 1 || dropped.Traces != 0 {
		t.Fatalf("Disown() = %+v, want 1 span and no whole trace", dropped)
	}
	if released := buffer.Expire(base.Add(time.Minute)); !slices.Equal(released, []string{"from-5"}) {
		t.Errorf("Expire() = %v, want the span from the partition still owned", released)
	}
}

// Disowning is not shutdown: a partition this worker keeps must go on being
// buffered, judged and released exactly as before.
func TestDisowningPartitionsNobodyOwnedChangesNothing(t *testing.T) {
	buffer := newBuffer(t, keepEverythingSlow(), defaultOptions())
	buffer.Add(base, factsOn(5, "trace-staying"), "staying-1")

	if dropped := buffer.Disown(nil); dropped.Spans != 0 {
		t.Errorf("Disown(nil) dropped %d spans", dropped.Spans)
	}
	if dropped := buffer.Disown([]int32{7}); dropped.Spans != 0 {
		t.Errorf("Disown() of an unbuffered partition dropped %d spans", dropped.Spans)
	}
	if released := buffer.Expire(base.Add(time.Minute)); !slices.Equal(released, []string{"staying-1"}) {
		t.Errorf("Expire() = %v, want the untouched span", released)
	}
}

// Drain is what a planned shutdown calls, and it must not resurrect what a
// handover already gave away: those rows belong to the worker that took the
// partition, and writing them here is the duplicate all over again.
func TestDrainDoesNotWriteWhatAHandoverGaveAway(t *testing.T) {
	buffer := newBuffer(t, keepEverythingSlow(), defaultOptions())
	buffer.Add(base, factsOn(3, "trace-leaving"), "leaving-1")
	buffer.Add(base, factsOn(5, "trace-staying"), "staying-1")

	buffer.Disown([]int32{3})

	if released := buffer.Drain(base); !slices.Equal(released, []string{"staying-1"}) {
		t.Errorf("Drain() = %v, want only what this worker still owns", released)
	}
}
