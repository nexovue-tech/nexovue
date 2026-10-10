package sample_test

import (
	"fmt"
	"math/rand/v2"
	"slices"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

var base = time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)

// maxRandomness is the largest W3C randomness value: 56 bits all set.
const maxRandomness = uint64(1)<<56 - 1

// traceIDWithRandomness builds a 32 hex digit W3C trace id whose right-most 7
// bytes — the randomness a sampler reads — hold the given value.
func traceIDWithRandomness(randomness uint64) string {
	const nonRandomHalf = "0123456789abcdef00" // the 18 digits left of the randomness
	return fmt.Sprintf("%s%014x", nonRandomHalf, randomness&maxRandomness)
}

func defaultOptions() sample.Options {
	return sample.Options{
		Window:      10 * time.Second,
		DecisionTTL: time.Minute,
		MaxTraces:   1000,
		MaxSpans:    10000,
		MaxBytes:    1 << 30,
	}
}

// newBuffer builds a buffer over one policy, which is what a deployment with no
// per-project configuration runs.
func newBuffer(t *testing.T, policy sample.Policy, options sample.Options) *sample.Buffer[string] {
	t.Helper()
	buffer, err := sample.New[string](options, sample.StaticPolicy(policy))
	if err != nil {
		t.Fatalf("sample.New() = %v, want no error", err)
	}
	return buffer
}

func keepEverythingSlow() sample.Policy {
	return sample.DefaultPolicy(2*time.Second, 0)
}

func TestATraceIsJudgedOnlyOnceItsWindowHasClosed(t *testing.T) {
	buffer := newBuffer(t, sample.DefaultPolicy(2*time.Second, 100), defaultOptions())

	if released := buffer.Add(base, facts("trace-a", 0, false), "span-1"); len(released) != 0 {
		t.Fatalf("Add() released %d rows, want the span held until the window closes", len(released))
	}
	if released := buffer.Expire(base.Add(9 * time.Second)); len(released) != 0 {
		t.Fatalf("Expire() released %d rows before the window closed", len(released))
	}
	released := buffer.Expire(base.Add(10 * time.Second))
	if len(released) != 1 || released[0] != "span-1" {
		t.Fatalf("Expire() = %v, want the buffered span once the window closed", released)
	}
}

func TestPolicyKeepsWhatSomeoneWouldOpen(t *testing.T) {
	cases := map[string]struct {
		policy   sample.Policy
		errored  bool
		duration time.Duration
		want     bool
	}{
		"error trace, none of the healthy ones kept": {
			policy: keepEverythingSlow(), errored: true, want: true,
		},
		"slow trace, none of the healthy ones kept": {
			policy: keepEverythingSlow(), duration: 3 * time.Second, want: true,
		},
		"trace exactly at the threshold": {
			policy: keepEverythingSlow(), duration: 2 * time.Second, want: true,
		},
		"healthy trace at zero percent": {
			policy: keepEverythingSlow(), duration: time.Millisecond, want: false,
		},
		"healthy trace at a hundred percent": {
			policy: sample.DefaultPolicy(2*time.Second, 100), want: true,
		},
		"error trace when errors are not kept": {
			policy:  sample.Policy{KeepErrors: false, SlowThreshold: 2 * time.Second},
			errored: true, want: false,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			buffer := newBuffer(t, tc.policy, defaultOptions())
			buffer.Add(base, facts("trace-a", tc.duration, tc.errored), "span-1")

			released := buffer.Expire(base.Add(11 * time.Second))
			if kept := len(released) == 1; kept != tc.want {
				t.Errorf("released %v; kept = %v, want %v", released, kept, tc.want)
			}
		})
	}
}

// One errored span makes the whole trace worth keeping, which is the only
// reason the buffer exists.
func TestOneFailedSpanKeepsEverySpanOfItsTrace(t *testing.T) {
	buffer := newBuffer(t, keepEverythingSlow(), defaultOptions())

	buffer.Add(base, facts("trace-a", time.Millisecond, false), "root")
	buffer.Add(base, facts("trace-a", time.Millisecond, true), "failed-child")
	buffer.Add(base, facts("trace-a", time.Millisecond, false), "healthy-child")

	released := buffer.Expire(base.Add(11 * time.Second))
	if len(released) != 3 {
		t.Errorf("Expire() released %d rows, want all 3 spans of the failed trace", len(released))
	}
}

// Slow means the whole trace, earliest start to latest end. A root that
// returns before its children finish is fast on its own and slow as a trace,
// and the trace is what someone opens.
func TestSlowIsMeasuredAcrossEverySpanHeld(t *testing.T) {
	buffer := newBuffer(t, keepEverythingSlow(), defaultOptions())

	root := facts("trace-a", time.Second, false) // well under the 2s threshold
	child := facts("trace-a", 3*time.Second, false)
	child.Start = base.Add(500 * time.Millisecond) // still running when the root returned

	buffer.Add(base, root, "root")
	buffer.Add(base, child, "child")

	released := buffer.Expire(base.Add(11 * time.Second))
	if len(released) != 2 {
		t.Errorf("released %v, want both spans: the trace ran 3.5s against a 2s threshold",
			released)
	}
}

// A span arriving after its trace was decided has to follow that decision, or a
// dropped trace leaves orphan spans behind and a kept one loses a leg.
func TestALateSpanFollowsItsTracesDecision(t *testing.T) {
	cases := map[string]struct {
		errored  bool
		wantKept bool
	}{
		"late span of a kept trace":    {errored: true, wantKept: true},
		"late span of a dropped trace": {errored: false, wantKept: false},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			buffer := newBuffer(t, keepEverythingSlow(), defaultOptions())
			buffer.Add(base, facts("trace-a", time.Millisecond, tc.errored), "first")
			buffer.Expire(base.Add(11 * time.Second))

			late := buffer.Add(base.Add(12*time.Second), facts("trace-a", time.Millisecond, false), "late")
			if kept := len(late) == 1; kept != tc.wantKept {
				t.Errorf("late span released = %v, want kept = %v", late, tc.wantKept)
			}
		})
	}
}

// The healthy verdict is a comparison against the trace's own randomness — the
// W3C form — so it lands where any other sampler using the same rule lands.
// The threshold at 10% rejects the first 90% of the range, which is every
// randomness value below 0xe6666666666667.
func TestTheHealthyVerdictComesFromTheTracesOwnRandomness(t *testing.T) {
	cases := map[string]struct {
		randomness uint64
		percent    int
		want       bool
	}{
		"the top of the range is kept at 10%":     {randomness: maxRandomness, percent: 10, want: true},
		"exactly at the 10% threshold is kept":    {randomness: 0xe6666666666667, percent: 10, want: true},
		"one below the 10% threshold is dropped":  {randomness: 0xe6666666666666, percent: 10, want: false},
		"the bottom of the range is dropped":      {randomness: 0, percent: 10, want: false},
		"the bottom of the range is kept at 100%": {randomness: 0, percent: 100, want: true},
		"the top of the range is dropped at 0%":   {randomness: maxRandomness, percent: 0, want: false},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			buffer := newBuffer(t, sample.DefaultPolicy(time.Hour, tc.percent), defaultOptions())
			buffer.Add(base, facts(traceIDWithRandomness(tc.randomness), time.Millisecond, false), "span")

			released := buffer.Expire(base.Add(11 * time.Second))
			if kept := len(released) == 1; kept != tc.want {
				t.Errorf("randomness %014x at %d%%: kept = %v, want %v",
					tc.randomness, tc.percent, kept, tc.want)
			}
		})
	}
}

// An upstream sampler that published an r-value already decided against it.
// Following it rather than the trace id is what makes the two agree.
func TestAnExplicitRValueOverridesTheTraceID(t *testing.T) {
	cases := map[string]struct {
		traceState string
		want       bool
	}{
		"no tracestate falls back to the trace id": {traceState: "", want: true},
		"an r-value below the threshold drops a trace the id would keep": {
			traceState: "ot=rv:00000000000000", want: false,
		},
		"an r-value beside other fields is still found": {
			traceState: "vendor=x,ot=th:c;rv:ffffffffffffff", want: true,
		},
		"a malformed r-value falls back to the trace id": {
			traceState: "ot=rv:xyz", want: true,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			buffer := newBuffer(t, sample.DefaultPolicy(time.Hour, 10), defaultOptions())
			span := facts(traceIDWithRandomness(maxRandomness), time.Millisecond, false)
			span.TraceState = tc.traceState
			buffer.Add(base, span, "span")

			released := buffer.Expire(base.Add(11 * time.Second))
			if kept := len(released) == 1; kept != tc.want {
				t.Errorf("tracestate %q: kept = %v, want %v", tc.traceState, kept, tc.want)
			}
		})
	}
}

// The verdict for a healthy trace is a property of the trace, so a worker that
// has forgotten it entirely still agrees with the one that decided it. That
// holds for ids carrying W3C randomness and for the hashed fallback alike.
func TestTheHealthyVerdictIsAPropertyOfTheTrace(t *testing.T) {
	const traces = 200

	random := rand.New(rand.NewPCG(3, 4))
	w3c := make([]string, traces)
	legacy := make([]string, traces)
	for index := range traces {
		w3c[index] = traceIDWithRandomness(random.Uint64())
		legacy[index] = fmt.Sprintf("trace-%03d", index)
	}

	for name, ids := range map[string][]string{
		"trace ids carrying W3C randomness":   w3c,
		"trace ids with no usable randomness": legacy,
	} {
		t.Run(name, func(t *testing.T) {
			policy := sample.DefaultPolicy(time.Hour, 10)
			options := defaultOptions()
			options.DecisionTTL = time.Nanosecond // forget every verdict immediately

			// Each span carries its own trace id as its row, so what comes back
			// names exactly which traces were kept.
			keptIDs := func(buffer *sample.Buffer[string], at time.Time) []string {
				for _, id := range ids {
					buffer.Add(at, facts(id, time.Millisecond, false), id)
				}
				return buffer.Expire(at.Add(11 * time.Second))
			}

			reused := newBuffer(t, policy, options)
			first := keptIDs(reused, base)
			again := keptIDs(reused, base.Add(time.Hour))
			restarted := keptIDs(newBuffer(t, policy, options), base)

			if len(first) == 0 || len(first) == traces {
				t.Fatalf("kept %d of %d; a test that keeps all or none proves nothing",
					len(first), traces)
			}
			if !slices.Equal(first, again) {
				t.Errorf("the same buffer kept %v, then %v; the verdict drifted", first, again)
			}
			if !slices.Equal(first, restarted) {
				t.Errorf("a restarted worker kept %v, want the same %v", restarted, first)
			}
		})
	}
}

func TestHealthyTracesAreKeptAtRoughlyTheConfiguredRate(t *testing.T) {
	const traces = 50000
	const percent = 10

	// Trace ids drawn the way an SDK draws them, so the test exercises the
	// distribution of the sampler and not the distribution of a counter.
	random := rand.New(rand.NewPCG(1, 2))
	ids := map[string]func() string{
		"trace ids carrying W3C randomness": func() string {
			return traceIDWithRandomness(random.Uint64())
		},
		"trace ids with no usable randomness": func() string {
			return fmt.Sprintf("legacy-trace-%016x", random.Uint64())
		},
	}

	for name, nextID := range ids {
		t.Run(name, func(t *testing.T) {
			buffer := newBuffer(t, sample.DefaultPolicy(time.Hour, percent), sample.Options{
				Window:      10 * time.Second,
				DecisionTTL: time.Minute,
				MaxTraces:   traces + 1,
				MaxSpans:    traces + 1,
				MaxBytes:    1 << 30,
			})
			for range traces {
				buffer.Add(base, facts(nextID(), time.Millisecond, false), "span")
			}
			kept := len(buffer.Expire(base.Add(11 * time.Second)))

			// The tolerance is for the distribution, not for a sampler that is
			// quietly keeping everything or nothing.
			t.Logf("kept %d of %d (%.2f%%)", kept, traces, 100*float64(kept)/traces)
			if kept < traces*9/100 || kept > traces*11/100 {
				t.Errorf("kept %d of %d healthy traces, want roughly %d%%", kept, traces, percent)
			}
		})
	}
}

// A buffer that grows without bound is an outage waiting for a traffic spike.
// Closing traces early is the right failure: a worse sample, never lost data.
func TestAFullBufferClosesTheOldestTracesEarly(t *testing.T) {
	buffer := newBuffer(t, sample.DefaultPolicy(time.Hour, 100), sample.Options{
		Window:      time.Minute,
		DecisionTTL: time.Minute,
		MaxTraces:   10,
		MaxSpans:    10,
		MaxBytes:    1 << 30,
	})

	released := 0
	for index := range 25 {
		released += len(buffer.Add(base, facts(fmt.Sprintf("trace-%02d", index), 0, false), "span"))
	}

	stats := buffer.Stats()
	if stats.OpenTraces > 10 {
		t.Errorf("OpenTraces = %d, want at most the configured 10", stats.OpenTraces)
	}
	if stats.ClosedEarly == 0 {
		t.Error("ClosedEarly = 0, want the overflow to be visible in the stats")
	}
	if released+stats.OpenTraces != 25 {
		t.Errorf("released %d and still hold %d of 25 traces; some were lost",
			released, stats.OpenTraces)
	}
}

// Shutdown must not leave spans in memory: an unwritten span is data lost
// between the broker and the store.
func TestDrainReleasesEveryKeptSpanStillHeld(t *testing.T) {
	buffer := newBuffer(t, sample.DefaultPolicy(time.Hour, 100), defaultOptions())
	buffer.Add(base, facts("trace-a", 0, false), "a")
	buffer.Add(base, facts("trace-b", 0, false), "b")

	if drained := buffer.Drain(base); len(drained) != 2 {
		t.Errorf("Drain() = %v, want both held spans", drained)
	}
	if held := buffer.Stats().BufferedSpans; held != 0 {
		t.Errorf("BufferedSpans = %d after Drain(), want 0", held)
	}
}

func TestPolicyFromJSONLayersOverTheDeploymentDefault(t *testing.T) {
	fallback := sample.DefaultPolicy(2*time.Second, 10)
	cases := map[string]struct {
		document  string
		want      sample.Policy
		wantError bool
	}{
		"absent document keeps the default": {document: "", want: fallback},
		"empty document keeps the default":  {document: `{}`, want: fallback},
		"percent only": {
			document: `{"healthy_percent":50}`,
			want:     sample.Policy{KeepErrors: true, SlowThreshold: 2 * time.Second, HealthyPercent: 50},
		},
		"threshold only": {
			document: `{"slow_threshold_ms":500}`,
			want:     sample.Policy{KeepErrors: true, SlowThreshold: 500 * time.Millisecond, HealthyPercent: 10},
		},
		"errors switched off": {
			document: `{"keep_errors":false}`,
			want:     sample.Policy{KeepErrors: false, SlowThreshold: 2 * time.Second, HealthyPercent: 10},
		},
		"percentage above a hundred is refused": {document: `{"healthy_percent":140}`, wantError: true},
		"negative percentage is refused":        {document: `{"healthy_percent":-1}`, wantError: true},
		"negative threshold is refused":         {document: `{"slow_threshold_ms":-1}`, wantError: true},
		"malformed document is refused":         {document: `{"healthy_percent":`, wantError: true},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			got, err := sample.PolicyFromJSON([]byte(tc.document), fallback)
			if tc.wantError {
				if err == nil {
					t.Fatalf("PolicyFromJSON(%s) succeeded, want an error", tc.document)
				}
				return
			}
			if err != nil {
				t.Fatalf("PolicyFromJSON(%s) = %v, want no error", tc.document, err)
			}
			if got != tc.want {
				t.Errorf("PolicyFromJSON(%s) = %+v, want %+v", tc.document, got, tc.want)
			}
		})
	}
}

func TestNewRefusesAnUnboundedBuffer(t *testing.T) {
	cases := map[string]sample.Options{
		"no window":      {DecisionTTL: time.Minute, MaxTraces: 1, MaxSpans: 1, MaxBytes: 1},
		"no trace limit": {Window: time.Second, DecisionTTL: time.Minute, MaxSpans: 1, MaxBytes: 1},
		"no span limit":  {Window: time.Second, DecisionTTL: time.Minute, MaxTraces: 1, MaxBytes: 1},
		"no byte limit":  {Window: time.Second, DecisionTTL: time.Minute, MaxTraces: 1, MaxSpans: 1},
	}
	for name, options := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := sample.New[string](options, sample.StaticPolicy(keepEverythingSlow())); err == nil {
				t.Errorf("sample.New(%+v) succeeded, want a refusal", options)
			}
		})
	}
}

func facts(traceID string, duration time.Duration, errored bool) sample.Facts {
	return sample.Facts{
		TraceID:   traceID,
		ProjectID: "project-1",
		Start:     base,
		Duration:  duration,
		Errored:   errored,
	}
}

// The counts alone allowed 500,000 held spans against a 1 GiB worker, and one
// span can carry megabytes: the buffer is bounded by what it holds, too. When
// the bytes run out the oldest trace closes early, decided on what has arrived,
// exactly as the count limits already do — nothing is lost, only judged sooner.
func TestTheBufferIsBoundedInBytesAsWellAsCount(t *testing.T) {
	const megabyte = 1 << 20
	buffer := newBuffer(t, keepEverythingSlow(), sample.Options{
		Window:      time.Hour,
		DecisionTTL: time.Minute,
		MaxTraces:   1000,
		MaxSpans:    1000,
		MaxBytes:    10 * megabyte,
	})

	released := 0
	for i := range 50 {
		heavy := facts(fmt.Sprintf("%032x", i+1), time.Millisecond, true) // errored: always kept
		heavy.Bytes = megabyte
		released += len(buffer.Add(base, heavy, "span"))
		if held := buffer.Stats().BufferedBytes; held > 10*megabyte {
			t.Fatalf("buffer holds %d bytes, past its %d-byte bound", held, 10*megabyte)
		}
	}
	if released != 40 {
		t.Errorf("released %d spans early, want 40: fifty 1 MiB traces into a 10 MiB buffer", released)
	}
	if closed := buffer.Stats().ClosedEarly; closed != 40 {
		t.Errorf("closed %d traces early, want 40 — the bound is a judgement brought forward, never a drop", closed)
	}
}
