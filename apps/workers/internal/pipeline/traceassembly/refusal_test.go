package traceassembly

import (
	"context"
	"errors"
	"log/slog"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
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
