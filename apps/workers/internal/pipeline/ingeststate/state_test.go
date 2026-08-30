package ingeststate_test

import (
	"math"
	"testing"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

// settled is the range every scenario below is measured against.
var settled = ingeststate.Range{Min: 10, Max: 20}

func TestClassifyNamesTheConnectorsFourScenarios(t *testing.T) {
	cases := map[string]struct {
		incoming ingeststate.Range
		want     ingeststate.Scenario
	}{
		"identical range":            {ingeststate.Range{Min: 10, Max: 20}, ingeststate.Same},
		"same start, reaches past":   {ingeststate.Range{Min: 10, Max: 30}, ingeststate.Overlapping},
		"starts inside, ends past":   {ingeststate.Range{Min: 15, Max: 30}, ingeststate.Overlapping},
		"wholly inside":              {ingeststate.Range{Min: 12, Max: 18}, ingeststate.Contains},
		"inside and ends at the max": {ingeststate.Range{Min: 12, Max: 20}, ingeststate.Contains},
		"begins past the max":        {ingeststate.Range{Min: 21, Max: 30}, ingeststate.New},
		"far past the max":           {ingeststate.Range{Min: 99, Max: 120}, ingeststate.New},

		// The connector discards the part of a batch below the settled range
		// before classifying, so these are judged on what is left of them.
		"starts below, ends inside":  {ingeststate.Range{Min: 5, Max: 15}, ingeststate.Contains},
		"starts below, ends at max":  {ingeststate.Range{Min: 5, Max: 20}, ingeststate.Same},
		"starts below, reaches past": {ingeststate.Range{Min: 5, Max: 30}, ingeststate.Overlapping},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := ingeststate.Classify(settled, tc.incoming); got != tc.want {
				t.Errorf("Classify(%v, %v) = %s, want %s", settled, tc.incoming, got, tc.want)
			}
		})
	}
}

// The whole state machine: every overlap scenario crossed with how far the
// previous settlement got. An offset resolved the wrong way is either a double
// count or a hole in the counters, so each case names which offsets survive.
func TestResolveCoversEveryScenarioAgainstEveryPreviousState(t *testing.T) {
	cases := map[string]struct {
		flag      ingeststate.Flag
		incoming  ingeststate.Range
		wantFrom  int64
		wantScene ingeststate.Scenario
	}{
		"same batch, stored": {
			flag: ingeststate.After, incoming: ingeststate.Range{Min: 10, Max: 20},
			wantFrom: 21, wantScene: ingeststate.Same,
		},
		"same batch, in flight": {
			flag: ingeststate.Before, incoming: ingeststate.Range{Min: 10, Max: 20},
			wantFrom: 10, wantScene: ingeststate.Same,
		},
		"overlapping batch, stored": {
			flag: ingeststate.After, incoming: ingeststate.Range{Min: 10, Max: 30},
			wantFrom: 21, wantScene: ingeststate.Overlapping,
		},
		"overlapping batch, in flight": {
			flag: ingeststate.Before, incoming: ingeststate.Range{Min: 10, Max: 30},
			wantFrom: 10, wantScene: ingeststate.Overlapping,
		},
		"contained batch, stored": {
			flag: ingeststate.After, incoming: ingeststate.Range{Min: 12, Max: 18},
			wantFrom: 21, wantScene: ingeststate.Contains,
		},
		// Deviation from the connector, which parks this case on a dead-letter
		// queue. Counting it again risks a duplicate; parking it guarantees a
		// hole, and a hole is the worse answer.
		"contained batch, in flight": {
			flag: ingeststate.Before, incoming: ingeststate.Range{Min: 12, Max: 18},
			wantFrom: 10, wantScene: ingeststate.Contains,
		},
		"new batch, stored": {
			flag: ingeststate.After, incoming: ingeststate.Range{Min: 21, Max: 30},
			wantFrom: 21, wantScene: ingeststate.New,
		},
		// New data is past the settled range either way: nothing about it is in
		// doubt, whatever became of the settlement before it.
		"new batch, in flight": {
			flag: ingeststate.Before, incoming: ingeststate.Range{Min: 21, Max: 30},
			wantFrom: 21, wantScene: ingeststate.New,
		},
	}

	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			previous := ingeststate.State{Range: settled, Flag: tc.flag}
			got := ingeststate.Resolve(&previous, tc.incoming)

			if got.CountFrom != tc.wantFrom {
				t.Errorf("CountFrom = %d, want %d", got.CountFrom, tc.wantFrom)
			}
			if got.Scenario != tc.wantScene {
				t.Errorf("Scenario = %s, want %s", got.Scenario, tc.wantScene)
			}
			wantAmbiguous := tc.flag == ingeststate.Before && tc.wantScene != ingeststate.New
			if got.Ambiguous != wantAmbiguous {
				t.Errorf("Ambiguous = %v, want %v", got.Ambiguous, wantAmbiguous)
			}
		})
	}
}

func TestResolveWithNoStateCountsEverything(t *testing.T) {
	decision := ingeststate.Resolve(nil, ingeststate.Range{Min: 0, Max: 10})

	if decision.Scenario != ingeststate.New {
		t.Errorf("Scenario = %s, want %s", decision.Scenario, ingeststate.New)
	}
	for _, offset := range []int64{0, 5, 10, math.MinInt64} {
		if !decision.Counts(offset) {
			t.Errorf("offset %d was not counted, but nothing has ever been settled", offset)
		}
	}
}

// A confirmed settlement must exclude every offset it covers and no more: one
// too few is a double count, one too many is a hole.
func TestAConfirmedSettlementResumesAtTheNextOffset(t *testing.T) {
	previous := ingeststate.State{Range: settled, Flag: ingeststate.After}
	decision := ingeststate.Resolve(&previous, ingeststate.Range{Min: 10, Max: 25})

	for _, offset := range []int64{9, 10, 19, 20} {
		if decision.Counts(offset) {
			t.Errorf("offset %d was counted again after its settlement was confirmed", offset)
		}
	}
	for _, offset := range []int64{21, 25} {
		if !decision.Counts(offset) {
			t.Errorf("offset %d was skipped, but it was never counted", offset)
		}
	}
}

// An offset below an unconfirmed range belongs to an earlier settlement, which
// was confirmed before this one was ever written.
func TestAnUnconfirmedSettlementRecountsOnlyItsOwnRange(t *testing.T) {
	previous := ingeststate.State{Range: settled, Flag: ingeststate.Before}
	decision := ingeststate.Resolve(&previous, ingeststate.Range{Min: 5, Max: 25})

	if decision.Counts(9) {
		t.Error("an offset below the unconfirmed range was counted again")
	}
	for _, offset := range []int64{10, 20, 25} {
		if !decision.Counts(offset) {
			t.Errorf("offset %d was skipped while its settlement was still unconfirmed", offset)
		}
	}
}

func TestDecisionsCountAPartitionNothingIsKnownAbout(t *testing.T) {
	known := ingeststate.Partition{Topic: "spans-raw", ID: 0}
	unknown := ingeststate.Partition{Topic: "spans-raw", ID: 7}
	decisions := ingeststate.Decisions{known: {CountFrom: 100}}

	if decisions.Counts(known, 99) {
		t.Error("an offset below CountFrom was counted")
	}
	if !decisions.Counts(known, 100) {
		t.Error("the offset at CountFrom was skipped")
	}
	if !decisions.Counts(unknown, 0) {
		t.Error("an unrecognised partition was skipped; that would be a hole, not a duplicate")
	}
}

func TestPartitionKeysRoundTrip(t *testing.T) {
	cases := map[string]ingeststate.Partition{
		"spans-raw:0":       {Topic: "spans-raw", ID: 0},
		"spans-raw:11":      {Topic: "spans-raw", ID: 11},
		"odd:topic:name:3":  {Topic: "odd:topic:name", ID: 3},
		"spans-raw:2147483": {Topic: "spans-raw", ID: 2147483},
	}
	for key, want := range cases {
		t.Run(key, func(t *testing.T) {
			if got := want.Key(); got != key {
				t.Errorf("Key() = %q, want %q", got, key)
			}
			got, err := ingeststate.ParsePartitionKey(key)
			if err != nil {
				t.Fatalf("ParsePartitionKey(%q): %v", key, err)
			}
			if got != want {
				t.Errorf("ParsePartitionKey(%q) = %+v, want %+v", key, got, want)
			}
		})
	}
}

func TestMalformedStoredValuesAreRefused(t *testing.T) {
	for _, key := range []string{"", "spans-raw", "spans-raw:", ":0", "spans-raw:x"} {
		if _, err := ingeststate.ParsePartitionKey(key); err == nil {
			t.Errorf("ParsePartitionKey(%q) succeeded; a malformed key must not be guessed at", key)
		}
	}
	for _, flag := range []string{"", "before", "DONE"} {
		if _, err := ingeststate.ParseFlag(flag); err == nil {
			t.Errorf("ParseFlag(%q) succeeded; an unknown state must not be guessed at", flag)
		}
	}
}

// The zero-value trap: a partition nothing is known about must write every
// offset, including offset zero. Kafka offsets start at zero, so a watermark
// left at Go's zero value would claim the first record was already stored.
func TestAPartitionWithNoStateWritesEveryOffset(t *testing.T) {
	decision := ingeststate.Resolve(nil, ingeststate.Range{Min: 0, Max: 99})
	for _, offset := range []int64{0, 1, 99} {
		if !decision.Stores(offset) {
			t.Errorf("offset %d was skipped although nothing is known about the partition", offset)
		}
	}
}

// NothingWritten is the value below the first offset, so a partition recorded
// as having stored nothing still writes offset zero.
func TestNothingWrittenStillWritesTheFirstOffset(t *testing.T) {
	settled := ingeststate.State{
		Range:          ingeststate.Range{Min: 0, Max: 99},
		Flag:           ingeststate.After,
		WrittenThrough: ingeststate.NothingWritten,
	}
	if !ingeststate.Resolve(&settled, ingeststate.Range{Min: 0, Max: 99}).Stores(0) {
		t.Error("offset 0 was skipped although no span had been written")
	}
}

// An unconfirmed counter settlement says nothing about the spans. The BEFORE
// flag re-counts, because a counter insert may or may not have landed; the span
// watermark is only ever written after its insert was acknowledged, so it is
// believed as it stands.
func TestAnUnconfirmedSettlementStillTrustsTheSpanWatermark(t *testing.T) {
	settled := ingeststate.State{
		Range:          ingeststate.Range{Min: 50, Max: 99},
		Flag:           ingeststate.Before,
		WrittenThrough: 120,
	}
	decision := ingeststate.Resolve(&settled, ingeststate.Range{Min: 50, Max: 199})

	if !decision.Ambiguous || decision.CountFrom != 50 {
		t.Fatalf("counter half = %+v, want the unconfirmed range recounted from 50", decision)
	}
	if decision.Stores(120) {
		t.Error("offset 120 was written again although the store acknowledged it")
	}
	if !decision.Stores(121) {
		t.Error("offset 121 was skipped although nothing had written it")
	}
}

// A partition no decision covers is written, for the same reason it is counted.
func TestDecisionsWriteAPartitionNothingIsKnownAbout(t *testing.T) {
	unknown := ingeststate.Partition{Topic: "spans-raw", ID: 7}
	var none ingeststate.Decisions
	if !none.Stores(unknown, 0) {
		t.Error("an unrecognised partition was skipped; a missing span cannot be repaired")
	}
}
