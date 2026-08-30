package latencysample

import (
	"encoding/binary"
	"sort"
	"testing"
)

func spanIDOf(n int) []byte {
	id := make([]byte, 8)
	binary.BigEndian.PutUint64(id, uint64(n))
	return id
}

func TestReservoirKeepsEverythingUnderTheCap(t *testing.T) {
	var samples Reservoir
	for n := 0; n < 100; n++ {
		samples.Observe(Weigh(nil, spanIDOf(n)), float64(100-n))
	}

	values := samples.Values()
	if len(values) != 100 {
		t.Fatalf("expected all 100 durations kept, got %d", len(values))
	}
	if !sort.Float64sAreSorted(values) {
		t.Fatalf("values reached the store unsorted: %v", values)
	}
}

// Above the cap the sample is the lowest-weighted durations — a fixed subset of
// the spans, not a random one, so the same minute re-derived produces the same
// block.
func TestReservoirKeepsTheLowestWeightedDurations(t *testing.T) {
	const observed = MaxSamples * 3

	weights := make([]uint64, 0, observed)
	var samples Reservoir
	for n := 0; n < observed; n++ {
		weight := Weigh(nil, spanIDOf(n))
		weights = append(weights, weight)
		samples.Observe(weight, float64(n))
	}
	if got := len(samples.Values()); got != MaxSamples {
		t.Fatalf("expected the sample capped at %d, got %d", MaxSamples, got)
	}

	sort.Slice(weights, func(i, j int) bool { return weights[i] < weights[j] })
	cutoff := weights[MaxSamples-1]
	for _, kept := range samples.kept {
		if kept.weight > cutoff {
			t.Fatalf("kept weight %d above the cutoff %d", kept.weight, cutoff)
		}
	}
}

// Arrival order must not change the sample: a replay reads the same offsets but
// a rebalance can reorder what a single batch holds.
func TestReservoirDoesNotDependOnArrivalOrder(t *testing.T) {
	const observed = MaxSamples * 2

	forward := &Reservoir{}
	backward := &Reservoir{}
	for n := 0; n < observed; n++ {
		forward.Observe(Weigh(nil, spanIDOf(n)), float64(n))
	}
	for n := observed - 1; n >= 0; n-- {
		backward.Observe(Weigh(nil, spanIDOf(n)), float64(n))
	}

	first, second := forward.Values(), backward.Values()
	if len(first) != len(second) {
		t.Fatalf("different sample sizes: %d and %d", len(first), len(second))
	}
	for i := range first {
		if first[i] != second[i] {
			t.Fatalf("sample %d differs: %v vs %v", i, first[i], second[i])
		}
	}
}
