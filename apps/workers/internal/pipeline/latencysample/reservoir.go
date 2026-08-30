// Package latencysample keeps a bounded, unbiased and *reproducible* sample of
// one bucket's durations, for the per-minute rollups that store a quantile
// state.
//
// Reproducible is the hard requirement. A worker that crashes between the
// insert and the offset commit re-derives the same minute from the same
// offsets, and the store only recognises the repeat if the block is
// byte-identical (ADR 0060) — so the sample cannot come from a random
// generator. Each duration is weighted by a hash of its own span id and the
// smallest weights win: the same spans always produce the same sample, whatever
// order they arrive in.
//
// It lived in `serviceedges` until `derive` needed the same sample for
// `endpoint_stats_1m`. Two copies of a reservoir whose whole job is to be
// byte-identical is the shape of a defect waiting for one of them to be tuned.
package latencysample

import (
	"hash/fnv"
	"sort"
)

// MaxSamples bounds what one bucket keeps. It matches the reservoir
// ClickHouse's own `quantiles` holds, so the samples handed to the store are
// never thinned twice.
const MaxSamples = 8192

// Reservoir is the bounded sample itself. It is not safe for concurrent use:
// one pipeline goroutine owns the accumulator that owns it.
type Reservoir struct {
	// kept is a max-heap on weight, so the sample worth replacing is at the
	// front.
	kept []weighted
}

type weighted struct {
	weight uint64
	ms     float64
}

// Weigh derives a sample's weight from the identity of the span it came from.
func Weigh(traceID, spanID []byte) uint64 {
	digest := fnv.New64a()
	// hash.Hash documents that Write never returns an error, so the results are
	// discarded explicitly rather than checked into a path that cannot happen.
	_, _ = digest.Write(traceID)
	_, _ = digest.Write(spanID)
	return digest.Sum64()
}

// Observe offers one duration to the sample.
func (r *Reservoir) Observe(weight uint64, ms float64) {
	if len(r.kept) < MaxSamples {
		r.kept = append(r.kept, weighted{weight: weight, ms: ms})
		r.siftUp(len(r.kept) - 1)
		return
	}
	if weight >= r.kept[0].weight {
		return
	}
	r.kept[0] = weighted{weight: weight, ms: ms}
	r.siftDown(0)
}

// Values returns the sampled durations in ascending order. Sorted because the
// order reaches the store inside the quantile state, and a block that differs
// only in ordering is a block the store cannot recognise as a repeat.
func (r *Reservoir) Values() []float64 {
	values := make([]float64, len(r.kept))
	for i, sample := range r.kept {
		values[i] = sample.ms
	}
	sort.Float64s(values)
	return values
}

func (r *Reservoir) siftUp(child int) {
	for child > 0 {
		parent := (child - 1) / 2
		if r.kept[parent].weight >= r.kept[child].weight {
			return
		}
		r.kept[parent], r.kept[child] = r.kept[child], r.kept[parent]
		child = parent
	}
}

func (r *Reservoir) siftDown(parent int) {
	for {
		largest := parent
		for _, child := range [2]int{2*parent + 1, 2*parent + 2} {
			if child < len(r.kept) && r.kept[child].weight > r.kept[largest].weight {
				largest = child
			}
		}
		if largest == parent {
			return
		}
		r.kept[parent], r.kept[largest] = r.kept[largest], r.kept[parent]
		parent = largest
	}
}
