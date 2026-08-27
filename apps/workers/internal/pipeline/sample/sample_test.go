package sample_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/sample"
)

var base = time.Date(2026, 8, 27, 10, 0, 0, 0, time.UTC)

func defaultOptions() sample.Options {
	return sample.Options{
		Window:      10 * time.Second,
		DecisionTTL: time.Minute,
		MaxTraces:   1000,
		MaxSpans:    10000,
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

// The verdict for a healthy trace is a hash of its id, so a worker that has
// forgotten the trace entirely still agrees with the one that decided it.
func TestTheHealthyVerdictIsAPropertyOfTheTrace(t *testing.T) {
	policy := sample.DefaultPolicy(time.Hour, 10)
	options := defaultOptions()
	options.DecisionTTL = time.Nanosecond // forget every verdict immediately

	first := newBuffer(t, policy, options)
	second := newBuffer(t, policy, options)

	for index := range 200 {
		id := fmt.Sprintf("trace-%03d", index)
		first.Add(base, facts(id, time.Millisecond, false), "span")
		second.Add(base, facts(id, time.Millisecond, false), "span")
	}
	if a, b := len(first.Expire(base.Add(11*time.Second))), len(second.Expire(base.Add(11*time.Second))); a != b {
		t.Fatalf("two buffers kept %d and %d spans, want the same verdicts", a, b)
	}
}

func TestHealthyTracesAreKeptAtRoughlyTheConfiguredRate(t *testing.T) {
	const traces = 5000
	buffer := newBuffer(t, sample.DefaultPolicy(time.Hour, 10), sample.Options{
		Window:      10 * time.Second,
		DecisionTTL: time.Minute,
		MaxTraces:   traces + 1,
		MaxSpans:    traces + 1,
	})

	for index := range traces {
		buffer.Add(base, facts(fmt.Sprintf("trace-%05d", index), time.Millisecond, false), "span")
	}
	kept := len(buffer.Expire(base.Add(11 * time.Second)))

	// A hash is not a die: the tolerance is for the distribution, not for a
	// sampler that is quietly keeping everything or nothing.
	if kept < traces*8/100 || kept > traces*12/100 {
		t.Errorf("kept %d of %d healthy traces, want roughly 10%%", kept, traces)
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
		"no window":      {DecisionTTL: time.Minute, MaxTraces: 1, MaxSpans: 1},
		"no trace limit": {Window: time.Second, DecisionTTL: time.Minute, MaxSpans: 1},
		"no span limit":  {Window: time.Second, DecisionTTL: time.Minute, MaxTraces: 1},
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
