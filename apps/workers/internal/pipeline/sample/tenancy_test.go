package sample

import (
	"testing"
	"time"
)

type fixedPolicy struct{ p Policy }

func (f fixedPolicy) SamplingPolicy(string) Policy { return f.p }

func newTestBuffer(t *testing.T) *Buffer[string] {
	t.Helper()
	b, err := New[string](Options{
		Window: time.Second, DecisionTTL: time.Minute,
		MaxTraces: 100, MaxSpans: 1000,
	}, fixedPolicy{Policy{KeepErrors: true, SlowThreshold: time.Hour, HealthyPercent: 0}})
	if err != nil {
		t.Fatalf("buffer: %v", err)
	}
	return b
}

// Fixture check: an errored trace on its own must be kept.
func TestFixtureAnErroredTraceIsKept(t *testing.T) {
	now := time.Now()
	b := newTestBuffer(t)
	b.Add(now, Facts{TraceID: "aa", ProjectID: "solo", Start: now, Errored: true}, "row")
	kept := b.Expire(now.Add(5 * time.Second))
	if len(kept) == 0 {
		t.Fatal("fixture is wrong: an errored trace was not kept even alone")
	}
	t.Logf("errored trace kept %d row(s) — fixture is sound", len(kept))
}

// Two tenants can carry the same trace id: W3C trace context propagates across
// organisations, so Company A calling Company B forwards its traceparent and
// both export the same id to us.
func TestTwoTenantsSharingATraceIDDoNotCollide(t *testing.T) {
	const shared = "4bf92f3577b34da6a3ce929d0e0e4736"
	now := time.Now()
	b := newTestBuffer(t)

	// Tenant A's trace is healthy, and this policy keeps no healthy traces, so
	// its verdict is "drop" — and that verdict outlives the trace for DecisionTTL.
	b.Add(now, Facts{TraceID: shared, ProjectID: "tenant-a", Start: now}, "A-span")
	if kept := b.Expire(now.Add(5 * time.Second)); len(kept) != 0 {
		t.Fatalf("tenant A's healthy span was kept at 0%%; fixture problem: %v", kept)
	}

	// Tenant B's trace under the same id ERRORED. Every policy here keeps errors,
	// so losing it is unambiguous data loss, not a sampling decision.
	at := now.Add(6 * time.Second)
	written := b.Add(at, Facts{TraceID: shared, ProjectID: "tenant-b", Start: at, Errored: true}, "B-span")
	keptB := b.Expire(at.Add(5 * time.Second))

	if len(written)+len(keptB) == 0 {
		t.Errorf("CROSS-TENANT COLLISION: tenant B's ERRORED span was dropped because "+
			"tenant A's healthy trace had already been judged under the same id %q and "+
			"its verdict was cached. Errors are kept 100%%, so this is data loss, and it "+
			"breaks tenant isolation and no-loss at once. Two tenants sharing a trace id "+
			"is ordinary W3C propagation between customers, not an attack", shared)
	}
}
