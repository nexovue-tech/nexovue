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

	err := p.stepOverRefusal(context.Background(), "metrics_sum", 7,
		consume.AsPermanent(errors.New("INCORRECT_DATA (117)")))

	if err != nil {
		t.Fatalf("err = %v; a refused derived row must not stop the settlement", err)
	}
	if p.derivedRowsRefused != 7 {
		t.Errorf("derived_rows_refused = %d, want 7 — a silent drop is the thing to avoid", p.derivedRowsRefused)
	}
	outage := errors.New("dial tcp: connection refused")
	if got := p.stepOverRefusal(context.Background(), "metrics_sum", 7, outage); !errors.Is(got, outage) {
		t.Errorf("an outage was stepped over: %v", got)
	}
}
