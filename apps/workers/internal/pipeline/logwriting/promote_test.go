package logwriting

import (
	"context"
	"errors"
	"log/slog"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/consume"
	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/vitals"
)

// refusingPromoter is a store that will never take these readings, which is what
// `consume.Permanent` means.
type refusingPromoter struct{ calls int }

func (r *refusingPromoter) Insert(context.Context, []vitals.Reading, []vitals.Stat) error {
	r.calls++
	return consume.AsPermanent(errors.New("web_vitals refused the block"))
}

// unavailablePromoter is a store that is briefly not answering. Worth retrying,
// so the batch must be held rather than stepped over.
type unavailablePromoter struct{ calls int }

func (u *unavailablePromoter) Insert(context.Context, []vitals.Reading, []vitals.Stat) error {
	u.calls++
	return errors.New("dial tcp: connection refused")
}

type countingPromoter struct{ readings int }

func (c *countingPromoter) Insert(_ context.Context, readings []vitals.Reading, _ []vitals.Stat) error {
	c.readings += len(readings)
	return nil
}

func pipelineWithPromoter(promoter vitalPromoter) *Pipeline {
	return &Pipeline{
		block:       newLogBlock(),
		vitalWriter: promoter,
		vitalRollup: vitals.NewRollup(),
		log:         slog.New(slog.DiscardHandler),
	}
}

func oneReading() vitals.Reading {
	return vitals.Reading{VitalName: "lcp", VitalID: "a", Value: 1200}
}

// The failure this guards against has happened here before, on the log path:
// one malformed record stopped ingest for every tenant for 41 minutes. A
// browser record the store will not take must not be able to do the same thing,
// because `web_vitals` is a promoted copy and `logs` is the record itself.
func TestARefusedReadingDoesNotCostTheChunkItsLogRecords(t *testing.T) {
	promoter := &refusingPromoter{}
	pipeline := pipelineWithPromoter(promoter)
	pipeline.holdVitals([]vitals.Reading{oneReading()})

	refusal := promoter.Insert(context.Background(), pipeline.vitalReadings, nil)
	promoted, err := pipeline.afterPromoting(context.Background(), refusal)

	if err != nil {
		t.Fatalf("a permanent refusal was returned as an error: %v", err)
	}
	if promoted != 0 {
		t.Errorf("promoted = %d, want 0: nothing was stored", promoted)
	}
	if pipeline.vitalsRefused != 1 {
		t.Errorf("vitals_refused = %d, want 1 — a silent drop is the thing to avoid",
			pipeline.vitalsRefused)
	}
	if promoter.calls != 1 {
		t.Errorf("insert attempted %d times, want 1: a refusal is not worth retrying",
			promoter.calls)
	}
}

// The other half of the same decision: a store that is merely down is not a
// refusal, and stepping over it would lose readings a retry would have written.
func TestAStoreThatIsDownHoldsTheBatch(t *testing.T) {
	promoter := &unavailablePromoter{}
	pipeline := pipelineWithPromoter(promoter)
	pipeline.holdVitals([]vitals.Reading{oneReading()})

	outage := promoter.Insert(context.Background(), pipeline.vitalReadings, nil)
	promoted, err := pipeline.afterPromoting(context.Background(), outage)

	if err == nil {
		t.Fatal("an unreachable store was stepped over; those readings would be dropped")
	}
	if promoted != 0 {
		t.Errorf("promoted = %d, want 0", promoted)
	}
	if pipeline.vitalsRefused != 0 {
		t.Errorf("vitals_refused = %d, want 0: an outage is not a refusal",
			pipeline.vitalsRefused)
	}
}

func TestReadingsReachTheWriterAndTheRollupTogether(t *testing.T) {
	promoter := &countingPromoter{}
	pipeline := pipelineWithPromoter(promoter)
	pipeline.holdVitals([]vitals.Reading{oneReading(), oneReading()})

	stored := promoter.Insert(context.Background(), pipeline.vitalReadings, nil)
	promoted, err := pipeline.afterPromoting(context.Background(), stored)
	if err != nil {
		t.Fatalf("afterPromoting: %v", err)
	}
	if promoted != 2 || promoter.readings != 2 {
		t.Errorf("promoted = %d and writer saw %d, want 2 and 2", promoted, promoter.readings)
	}
	if pipeline.vitalsRefused != 0 {
		t.Errorf("vitals_refused = %d, want 0", pipeline.vitalsRefused)
	}
}

func TestAChunkWithNoReadingsSkipsTheWriterEntirely(t *testing.T) {
	promoter := &refusingPromoter{}
	pipeline := pipelineWithPromoter(promoter)

	promoted, err := pipeline.promoteVitals(context.Background())
	if err != nil || promoted != 0 {
		t.Fatalf("promoteVitals = (%d, %v), want (0, nil)", promoted, err)
	}
	if promoter.calls != 0 {
		t.Errorf("an empty chunk still called the store %d times", promoter.calls)
	}
	// It returned before reaching p.loop, which a bare Pipeline does not have —
	// so this also pins the early return that keeps an empty chunk cheap.
}
