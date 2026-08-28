package derive

import (
	"testing"
	"time"
)

// The regression this guards: Go randomises map iteration, so an unsorted drain
// produces a different block every call. ClickHouse deduplicates a retried insert
// by hashing the block, so a non-deterministic order means a worker restart
// re-counts the same spans into a block the store cannot recognise as a repeat.
func TestDrainIsByteReproducible(t *testing.T) {
	minute := time.Date(2026, 8, 27, 11, 23, 0, 0, time.UTC)

	now := minute
	build := func() []Point {
		counters := NewCounters(0)
		for _, service := range []string{"payments", "checkout", "storefront", "cart", "search"} {
			for _, route := range []string{"/a", "/b", "/c"} {
				counters.Observe(now, Span{
					Timestamp: minute.Add(time.Second), ReceivedAt: minute.Add(time.Second),
					TenantID: "t", ProjectID: "p",
					ServiceName: service, Endpoint: route, DurationMS: 5,
				})
			}
		}
		return counters.DrainAll()
	}

	first := build()
	if len(first) == 0 {
		t.Fatal("drained nothing; the fixture is wrong")
	}

	// Many rounds: a single comparison could pass on a lucky shuffle.
	for round := 0; round < 25; round++ {
		again := build()
		if len(again) != len(first) {
			t.Fatalf("round %d drained %d points, want %d", round, len(again), len(first))
		}
		for i := range first {
			if first[i] != again[i] {
				t.Fatalf("round %d differs at %d:\n  first: %+v\n  again: %+v",
					round, i, first[i], again[i])
			}
		}
	}
}
