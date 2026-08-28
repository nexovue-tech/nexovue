package serviceedges

import (
	"hash/fnv"
	"sort"
)

// maxLatencySamples bounds what one edge-minute keeps. It matches the reservoir
// ClickHouse's own `quantiles` holds, so the samples handed to the store are
// never thinned twice.
const maxLatencySamples = 8192

// latencySamples keeps a bounded, unbiased and *reproducible* sample of one
// edge-minute's client-side durations.
//
// Reproducible is the hard requirement. A worker that crashes between the
// insert and the offset commit re-derives the same minute from the same
// offsets, and the store only recognises the repeat if the block is
// byte-identical (ADR 0060) — so the sample cannot come from a random
// generator. Each duration is weighted by a hash of its own span id and the
// smallest weights win: the same spans always produce the same sample, whatever
// order they arrive in.
type latencySamples struct {
	// kept is a max-heap on weight, so the sample worth replacing is at the
	// front.
	kept []weightedLatency
}

type weightedLatency struct {
	weight uint64
	ms     float64
}

// weigh derives a sample's weight from the identity of the span it came from.
func weigh(traceID, spanID []byte) uint64 {
	digest := fnv.New64a()
	digest.Write(traceID)
	digest.Write(spanID)
	return digest.Sum64()
}

func (s *latencySamples) observe(weight uint64, ms float64) {
	if len(s.kept) < maxLatencySamples {
		s.kept = append(s.kept, weightedLatency{weight: weight, ms: ms})
		s.siftUp(len(s.kept) - 1)
		return
	}
	if weight >= s.kept[0].weight {
		return
	}
	s.kept[0] = weightedLatency{weight: weight, ms: ms}
	s.siftDown(0)
}

// values returns the sampled durations in ascending order. Sorted because the
// order reaches the store inside the quantile state, and a block that differs
// only in ordering is a block the store cannot recognise as a repeat.
func (s *latencySamples) values() []float64 {
	values := make([]float64, len(s.kept))
	for i, sample := range s.kept {
		values[i] = sample.ms
	}
	sort.Float64s(values)
	return values
}

func (s *latencySamples) siftUp(child int) {
	for child > 0 {
		parent := (child - 1) / 2
		if s.kept[parent].weight >= s.kept[child].weight {
			return
		}
		s.kept[parent], s.kept[child] = s.kept[child], s.kept[parent]
		child = parent
	}
}

func (s *latencySamples) siftDown(parent int) {
	for {
		largest := parent
		for _, child := range [2]int{2*parent + 1, 2*parent + 2} {
			if child < len(s.kept) && s.kept[child].weight > s.kept[largest].weight {
				largest = child
			}
		}
		if largest == parent {
			return
		}
		s.kept[parent], s.kept[largest] = s.kept[largest], s.kept[parent]
		parent = largest
	}
}
