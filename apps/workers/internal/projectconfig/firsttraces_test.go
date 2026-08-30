package projectconfig_test

import (
	"context"
	"errors"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/projectconfig"
)

// carveOut is the first-traces size these tests run with — small enough to
// exhaust, and not 1, so "the first one" cannot pass for "the first N".
const carveOut = 3

// A project the refresher has never seen is a project that has just been
// created, and its first traces are the ones onboarding is waiting for.
func TestAnUnknownProjectIsInsideItsCarveOut(t *testing.T) {
	cache := projectconfig.NewCache(defaults())

	if !cache.SamplingPolicy("brand-new", carveOut).KeepingFirstTraces {
		t.Error("KeepingFirstTraces = false for a project no refresh has seen, " +
			"want the carve-out open: this is the project onboarding is polling for")
	}
}

func TestACarveOutClosesOnTheControlPlanesCount(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	store := &fakeStore{rows: []projectconfig.Row{
		{ProjectID: "started", FirstTracesStored: carveOut - 1},
		{ProjectID: "finished", FirstTracesStored: carveOut},
	}}

	if err := newRefresher(store, cache).Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}

	if !cache.SamplingPolicy("started", carveOut).KeepingFirstTraces {
		t.Errorf("KeepingFirstTraces = false with %d of %d stored, want the carve-out open",
			carveOut-1, carveOut)
	}
	if cache.SamplingPolicy("finished", carveOut).KeepingFirstTraces {
		t.Errorf("KeepingFirstTraces = true with %d of %d stored, want the percentage back",
			carveOut, carveOut)
	}
}

// The count this process has not written yet still counts. Without that, a
// worker would believe the carve-out was empty for a whole refresh interval and
// store it again on every tick.
func TestTracesStoredSinceTheLastFlushCloseTheCarveOut(t *testing.T) {
	cache := projectconfig.NewCache(defaults())

	for range carveOut {
		if !cache.SamplingPolicy("new", carveOut).KeepingFirstTraces {
			t.Fatal("the carve-out closed before it had stored anything")
		}
		cache.RecordStoredTrace("new")
	}

	if cache.SamplingPolicy("new", carveOut).KeepingFirstTraces {
		t.Errorf("KeepingFirstTraces = true after %d traces stored in this process, "+
			"want it closed before the count has even been written", carveOut)
	}
}

// The flush adds; it never sets. Two workers' counts sum in the control plane,
// which is what makes the carve-out the project's and not each process's.
func TestARefreshWritesWhatWasStoredAndReadsTheTotalBack(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	// Another worker got there first with two of the three.
	store := &fakeStore{rows: []projectconfig.Row{{ProjectID: "shared", FirstTracesStored: 2}}}
	refresher := newRefresher(store, cache)

	cache.RecordStoredTrace("shared")
	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}

	if len(store.recorded) != 1 || store.recorded[0]["shared"] != 1 {
		t.Fatalf("the refresher wrote %v, want one trace for shared", store.recorded)
	}
	if got := store.rows[0].FirstTracesStored; got != 3 {
		t.Errorf("stored count = %d, want 2 + 1: a flush must add, not overwrite", got)
	}
	if cache.SamplingPolicy("shared", carveOut).KeepingFirstTraces {
		t.Error("KeepingFirstTraces = true once the shared count reached the carve-out")
	}
	// The delta was written, so the next flush must not write it again.
	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}
	if len(store.recorded) != 1 {
		t.Errorf("the refresher wrote %d times, want 1: a flushed count must not be written twice",
			len(store.recorded))
	}
}

// Losing the count would only reopen a carve-out that has already been paid
// for, so a failed write keeps it for the next tick rather than dropping it.
func TestAFailedWriteKeepsTheCountForTheNextRefresh(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	store := &fakeStore{writeErr: errors.New("postgres unreachable")}
	refresher := newRefresher(store, cache)

	cache.RecordStoredTrace("new")
	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want the read to still succeed", err)
	}
	if len(store.recorded) != 0 {
		t.Fatalf("the store recorded %v while it was failing", store.recorded)
	}

	store.writeErr = nil
	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}
	if len(store.recorded) != 1 || store.recorded[0]["new"] != 1 {
		t.Errorf("the refresher wrote %v after the store came back, want the trace it "+
			"could not write the first time", store.recorded)
	}
}

// A read that fails must not lose the counts either: they were taken out of the
// cache before the read, and the read failing is the same outage.
func TestAFailedReadStillKeepsTheStoredCount(t *testing.T) {
	cache := projectconfig.NewCache(defaults())
	store := &fakeStore{err: errors.New("postgres unreachable"),
		writeErr: errors.New("postgres unreachable")}
	refresher := newRefresher(store, cache)

	cache.RecordStoredTrace("new")
	if err := refresher.Refresh(context.Background()); err == nil {
		t.Fatal("Refresh() succeeded while the store was down, want the failure reported")
	}

	store.err, store.writeErr = nil, nil
	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}
	if len(store.recorded) != 1 || store.recorded[0]["new"] != 1 {
		t.Errorf("the refresher wrote %v after the outage, want the trace stored during it",
			store.recorded)
	}
}

// The sampler counts on the pipeline goroutine while the refresher flushes on
// its own, so every count either survives to be written or is still in the
// cache — never half of each. Run with -race, this is where a torn count shows.
func TestCountingAndFlushingRunConcurrently(t *testing.T) {
	const traces = 200

	cache := projectconfig.NewCache(defaults())
	store := &fakeStore{}
	refresher := newRefresher(store, cache)

	sampled := make(chan struct{})
	go func() {
		defer close(sampled)
		for range traces {
			cache.SamplingPolicy("busy", carveOut)
			cache.RecordStoredTrace("busy")
		}
	}()
	for range 20 {
		if err := refresher.Refresh(context.Background()); err != nil {
			t.Errorf("Refresh() = %v, want no error", err)
		}
	}
	<-sampled
	if err := refresher.Refresh(context.Background()); err != nil {
		t.Fatalf("Refresh() = %v, want no error", err)
	}

	var written int64
	for _, flush := range store.recorded {
		written += flush["busy"]
	}
	if written != traces {
		t.Errorf("the flushes wrote %d traces, want all %d: a count was lost or written twice",
			written, traces)
	}
}

// Zero is off: a deployment that does not want the carve-out gets the
// percentage for every project, including one that has stored nothing.
func TestZeroFirstTracesLeavesEveryCarveOutClosed(t *testing.T) {
	cache := projectconfig.NewCache(defaults())

	if cache.SamplingPolicy("brand-new", 0).KeepingFirstTraces {
		t.Error("KeepingFirstTraces = true with the carve-out switched off")
	}
}
