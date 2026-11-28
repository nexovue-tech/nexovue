package traceassembly

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
)

func TestARefusedDerivedInsertIsCountedAndSteppedOver(t *testing.T) {
	p := &Pipeline{log: slog.New(slog.DiscardHandler)}

	refused, err := p.stepOverRefusal(context.Background(), "metrics_sum", 7,
		consume.AsPermanent(errors.New("INCORRECT_DATA (117)")))

	if err != nil {
		t.Fatalf("err = %v; a refused derived row must not stop the settlement", err)
	}
	if p.derivedRowsRefused != 7 {
		t.Errorf("derived_rows_refused = %d, want 7 — a silent drop is the thing to avoid", p.derivedRowsRefused)
	}
	outage := errors.New("dial tcp: connection refused")
	if _, got := p.stepOverRefusal(context.Background(), "metrics_sum", 7, outage); !errors.Is(got, outage) {
		t.Errorf("an outage was stepped over: %v", got)
	}
	// The settlement's progress is held on it (C22), so the caller has to hear
	// of it, not only the log.
	if !refused {
		t.Error("a refusal was stepped over without telling the caller")
	}
}

func TestAStoredOrFailedDerivedInsertIsNotReportedAsRefused(t *testing.T) {
	p := &Pipeline{log: slog.New(slog.DiscardHandler)}

	for name, outcome := range map[string]error{
		"stored": nil,
		"outage": errors.New("dial tcp: connection refused"),
	} {
		if refused, _ := p.stepOverRefusal(context.Background(), "metrics_sum", 7, outcome); refused {
			t.Errorf("%s was reported as a refusal; it would hold progress for nothing", name)
		}
	}
}

// versionStore stands in for `service_versions_1m`: it answers every insert with
// the same outcome and counts the calls.
type versionStore struct {
	outcome error
	calls   int
}

func (s *versionStore) Insert(context.Context, []derive.VersionStat) error {
	s.calls++
	return s.outcome
}

// steppingClock moves on by step at every look, so a retry budget is spent
// without the test waiting for it.
type steppingClock struct {
	at   time.Time
	step time.Duration
}

func (c *steppingClock) now() time.Time {
	c.at = c.at.Add(c.step)
	return c.at
}

func pipelineWithVersions(store *versionStore, clock *steppingClock) *Pipeline {
	return &Pipeline{log: slog.New(slog.DiscardHandler), versions: store, clock: clock.now}
}

func versionRows(count int) derive.Derived {
	rows := make([]derive.VersionStat, count)
	for index := range rows {
		rows[index].Version = fmt.Sprintf("v%d", index)
	}
	return derive.Derived{Versions: rows}
}

// AC19 for the version table: a refusal is counted, stepped over, and does not
// hold detection's progress, because the version rollup is not detection's
// input (F28 D13).
func TestARefusedVersionInsertIsCountedAndHoldsNothing(t *testing.T) {
	store := &versionStore{outcome: consume.AsPermanent(errors.New("INCORRECT_DATA (117)"))}
	p := pipelineWithVersions(store, &steppingClock{at: invariantStart, step: time.Second})

	refused, err := p.insertDerived(context.Background(), versionRows(3), nil)

	if err != nil || refused {
		t.Errorf("insertDerived = (%v, %v), want (false, nil): progress must not be held", refused, err)
	}
	if p.derivedRowsRefused != 3 {
		t.Errorf("derived_rows_refused = %d, want 3", p.derivedRowsRefused)
	}
	if store.calls != 1 {
		t.Errorf("the refused insert was tried %d times, want once", store.calls)
	}
}

// AC54: with the version insert failing transiently past its own budget, the
// settlement still completes — no error, so the worker stays up, and nothing
// refused, so progress and the settled minute advance. The rows are counted as
// stepped over, and the next five minutes' inserts are skipped and counted,
// not retried on every drain.
func TestAVersionInsertThatKeepsFailingIsSteppedOverThenPaused(t *testing.T) {
	store := &versionStore{outcome: errors.New("dial tcp: connection refused")}
	clock := &steppingClock{at: invariantStart, step: 10 * time.Second}
	p := pipelineWithVersions(store, clock)

	refused, err := p.insertDerived(context.Background(), versionRows(4), nil)
	if err != nil || refused {
		t.Fatalf("insertDerived = (%v, %v), want (false, nil): a transient version failure "+
			"must neither end the worker nor hold progress", refused, err)
	}
	if p.versionRowsSteppedOver != 4 {
		t.Errorf("version_rows_stepped_over = %d, want 4", p.versionRowsSteppedOver)
	}
	tried := store.calls
	if tried < 2 {
		t.Errorf("the insert was tried %d times, want it retried within its budget", tried)
	}

	if _, err := p.insertDerived(context.Background(), versionRows(5), nil); err != nil {
		t.Fatalf("insertDerived during the pause = %v", err)
	}
	if store.calls != tried {
		t.Errorf("the insert was tried during the pause (%d calls, want %d)", store.calls, tried)
	}
	if p.versionRowsSkipped != 5 {
		t.Errorf("version_rows_skipped = %d, want 5", p.versionRowsSkipped)
	}

	clock.at = clock.at.Add(versionInsertPause)
	store.outcome = nil
	if _, err := p.insertDerived(context.Background(), versionRows(2), nil); err != nil {
		t.Fatalf("insertDerived after the pause = %v", err)
	}
	if store.calls != tried+1 {
		t.Errorf("after the pause the insert was tried %d more times, want once", store.calls-tried)
	}
}
