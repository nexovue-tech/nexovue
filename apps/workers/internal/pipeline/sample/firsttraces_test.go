package sample_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

// firstTraces is the carve-out size these tests run with. Small enough to
// exhaust in a test, and not 1, so "the first one" cannot pass for "the first N".
const firstTraces = 3

// unluckyTraceID is a trace id whose randomness is zero, which every percentage
// below 100 rejects. A healthy trace carrying it is dropped unless something
// other than the percentage keeps it — which is what these tests are about.
func unluckyTraceID(nth int) string {
	// The randomness is the right-most 14 hex digits, left at zero; the 18 to
	// its left only make each trace its own.
	return fmt.Sprintf("%018x%014x", nth+1, 0)
}

// countingSource is the fleet's shared count, which is what the control plane
// holds: one number per project, incremented by whichever worker stored a trace.
type countingSource struct {
	policy sample.Policy
	stored map[string]int
}

func newCountingSource(healthyPercent int) *countingSource {
	return &countingSource{
		policy: sample.DefaultPolicy(2*time.Second, healthyPercent),
		stored: map[string]int{},
	}
}

func (s *countingSource) SamplingPolicy(projectID string, carveOut int) sample.Policy {
	policy := s.policy
	policy.KeepingFirstTraces = carveOut > 0 && s.stored[projectID] < carveOut
	return policy
}

func (s *countingSource) RecordStoredTrace(projectID string) { s.stored[projectID]++ }

func firstTracesOptions(carveOut int) sample.Options {
	options := defaultOptions()
	options.FirstTraces = carveOut
	return options
}

func newCountingBuffer(t *testing.T, source *countingSource, carveOut int) *sample.Buffer[string] {
	t.Helper()
	buffer, err := sample.New[string](firstTracesOptions(carveOut), source)
	if err != nil {
		t.Fatalf("sample.New() = %v, want no error", err)
	}
	return buffer
}

// projectFacts is one healthy, fast span of its own trace — a customer's first
// hello-world request, which is the trace this whole carve-out exists for.
func projectFacts(projectID, traceID string) sample.Facts {
	return sample.Facts{TraceID: traceID, ProjectID: projectID, Start: base}
}

// The defect, stated as a test: at ADR 0010's 10% a new customer's first span
// had a nine in ten chance of never being stored, and the onboarding screen
// polls storage.
func TestANewProjectsFirstHealthyTraceIsStoredDespiteThePercentage(t *testing.T) {
	source := newCountingSource(10)
	buffer := newCountingBuffer(t, source, firstTraces)

	buffer.Add(base, projectFacts("new-project", unluckyTraceID(0)), "first-span")
	released := buffer.Expire(base.Add(11 * time.Second))

	if len(released) != 1 {
		t.Fatalf("Expire() released %d rows, want the project's first span stored", len(released))
	}
	if source.stored["new-project"] != 1 {
		t.Errorf("stored count = %d, want 1: the carve-out must count what it kept",
			source.stored["new-project"])
	}
}

// The carve-out is a carve-out, not a new default: once the project has stored
// its first traces the percentage decides again.
func TestThePercentageAppliesOnceTheFirstTracesAreStored(t *testing.T) {
	source := newCountingSource(10)
	buffer := newCountingBuffer(t, source, firstTraces)

	for trace := range firstTraces {
		buffer.Add(base, projectFacts("new-project", unluckyTraceID(trace)), "span")
	}
	kept := buffer.Expire(base.Add(11 * time.Second))
	if len(kept) != firstTraces {
		t.Fatalf("Expire() released %d rows, want all %d of the carve-out's traces", len(kept), firstTraces)
	}

	after := base.Add(time.Minute)
	buffer.Add(after, sample.Facts{
		TraceID: unluckyTraceID(firstTraces), ProjectID: "new-project", Start: after,
	}, "span-after")
	if released := buffer.Expire(after.Add(11 * time.Second)); len(released) != 0 {
		t.Errorf("Expire() released %d rows after the carve-out closed, want the 10%% "+
			"percentage to drop this healthy trace", len(released))
	}
	if source.stored["new-project"] != firstTraces {
		t.Errorf("stored count = %d, want it to stop at %d once the carve-out closed",
			source.stored["new-project"], firstTraces)
	}
}

// The count is the project's, not the process's. Two workers sharing one count
// must store N traces between them, not N each — which is the whole reason it
// lives in the control plane rather than in a map on each worker.
func TestTwoWorkersShareOneCarveOut(t *testing.T) {
	source := newCountingSource(10)
	workerA := newCountingBuffer(t, source, firstTraces)
	workerB := newCountingBuffer(t, source, firstTraces)

	stored := 0
	for trace := range firstTraces * 2 {
		worker := workerA
		if trace%2 == 1 {
			worker = workerB
		}
		// Each trace gets its own instant so the two workers interleave rather
		// than deciding in one batch.
		at := base.Add(time.Duration(trace) * time.Minute)
		worker.Add(at, sample.Facts{
			TraceID: unluckyTraceID(trace), ProjectID: "shared-project", Start: at,
		}, "span")
		stored += len(worker.Expire(at.Add(11 * time.Second)))
	}

	if stored != firstTraces {
		t.Errorf("two workers stored %d traces, want %d: a per-process count would have "+
			"stored %d", stored, firstTraces, firstTraces*2)
	}
}

// One project exhausting its carve-out must not close anyone else's.
func TestEachProjectHasItsOwnCarveOut(t *testing.T) {
	source := newCountingSource(10)
	buffer := newCountingBuffer(t, source, firstTraces)

	for trace := range firstTraces {
		buffer.Add(base, projectFacts("busy-project", unluckyTraceID(trace)), "span")
	}
	buffer.Expire(base.Add(11 * time.Second))

	at := base.Add(time.Minute)
	buffer.Add(at, sample.Facts{
		TraceID: unluckyTraceID(0), ProjectID: "quiet-project", Start: at,
	}, "quiet-span")
	if released := buffer.Expire(at.Add(11 * time.Second)); len(released) != 1 {
		t.Errorf("Expire() released %d rows for a project that has stored nothing, "+
			"want its own carve-out to keep its first trace", len(released))
	}
}

// Zero is off, and off must be the old behaviour exactly — which is what a
// worker with no control plane runs on, because nothing there can count.
func TestZeroFirstTracesLeavesThePercentageAlone(t *testing.T) {
	source := newCountingSource(10)
	buffer := newCountingBuffer(t, source, 0)

	buffer.Add(base, projectFacts("new-project", unluckyTraceID(0)), "first-span")
	if released := buffer.Expire(base.Add(11 * time.Second)); len(released) != 0 {
		t.Errorf("Expire() released %d rows with the carve-out off, want the percentage "+
			"to decide", len(released))
	}
	if source.stored["new-project"] != 0 {
		t.Errorf("stored count = %d, want nothing counted while the carve-out is off",
			source.stored["new-project"])
	}
}

// A worker with no control-plane connection samples on the deployment policy
// alone. Its carve-out must be off rather than permanently open: nothing can
// count what it stored, so an open one would never close.
func TestAStaticPolicyNeverKeepsEveryTrace(t *testing.T) {
	buffer := newBuffer(t, sample.DefaultPolicy(2*time.Second, 10), firstTracesOptions(firstTraces))

	buffer.Add(base, projectFacts("new-project", unluckyTraceID(0)), "first-span")
	if released := buffer.Expire(base.Add(11 * time.Second)); len(released) != 0 {
		t.Errorf("Expire() released %d rows with no control plane, want the percentage "+
			"to decide", len(released))
	}
}

func TestNewRefusesANegativeCarveOut(t *testing.T) {
	if _, err := sample.New[string](firstTracesOptions(-1), newCountingSource(10)); err == nil {
		t.Error("sample.New(FirstTraces: -1) succeeded, want a refusal")
	}
}
