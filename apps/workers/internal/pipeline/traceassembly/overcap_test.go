package traceassembly

import (
	"context"
	"errors"
	"log/slog"
	"testing"
	"time"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/derive"
)

// overCapStore stands in for `services_over_cap_1m`: it keeps what it was given
// and answers with one outcome.
type overCapStore struct {
	outcome  error
	inserted []derive.ServiceCap
}

func (s *overCapStore) Insert(_ context.Context, entries []derive.ServiceCap) error {
	if s.outcome == nil {
		s.inserted = append(s.inserted, entries...)
	}
	return s.outcome
}

func pipelineWithOverCap(store *overCapStore) *Pipeline {
	clock := &steppingClock{at: invariantStart, step: time.Second}
	return &Pipeline{log: slog.New(slog.DiscardHandler), overCap: store, clock: clock.now}
}

var overCapEntries = []derive.ServiceCap{
	{Minute: invariantStart, TenantID: "tenant-a", RetentionDays: 30, Services: 4},
	{Minute: invariantStart, TenantID: "tenant-b", RetentionDays: 30, Services: 9},
}

// Going over the cap is written at drain, one row per tenant-minute, and the
// operator's warning stays beside it (browser-door T3.3, decision 1).
func TestADrainOverTheCapWritesItsRecord(t *testing.T) {
	store := &overCapStore{}
	p := pipelineWithOverCap(store)

	refused, err := p.insertDerived(context.Background(), derive.Derived{ServicesOverCap: overCapEntries}, nil)

	if err != nil || refused {
		t.Fatalf("insertDerived = (%v, %v), want (false, nil)", refused, err)
	}
	if len(store.inserted) != len(overCapEntries) {
		t.Fatalf("wrote %+v, want every entry %+v", store.inserted, overCapEntries)
	}
	for i, want := range overCapEntries {
		if store.inserted[i] != want {
			t.Errorf("row %d = %+v, want %+v", i, store.inserted[i], want)
		}
	}
	if p.servicesOverCap != 13 {
		t.Errorf("services_over_cap = %d, want 13: the operator's counter stays", p.servicesOverCap)
	}
}

// A refused record is counted and stepped over like any derived row, and holds
// no progress: detection reads `service_stats_1m`, which the refusal leaves
// whole.
func TestARefusedOverCapRecordIsCountedAndHoldsNothing(t *testing.T) {
	store := &overCapStore{outcome: consume.AsPermanent(errors.New("INCORRECT_DATA (117)"))}
	p := pipelineWithOverCap(store)

	refused, err := p.insertDerived(context.Background(), derive.Derived{ServicesOverCap: overCapEntries}, nil)

	if err != nil || refused {
		t.Errorf("insertDerived = (%v, %v), want (false, nil): progress must not be held", refused, err)
	}
	if p.derivedRowsRefused != uint64(len(overCapEntries)) {
		t.Errorf("derived_rows_refused = %d, want %d", p.derivedRowsRefused, len(overCapEntries))
	}
}
