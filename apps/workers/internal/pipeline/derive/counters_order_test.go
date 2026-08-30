package derive

import (
	"fmt"
	"testing"
	"time"
)

// The regression this guards: Go randomises map iteration, so an unsorted drain
// produces a different block every call. ClickHouse deduplicates a retried
// insert by the token derived from that block, so a non-deterministic order
// means a worker restart re-counts the same spans under a token the store has
// never seen — and both rollups sum the same minute in twice.
func TestDrainIsByteReproducible(t *testing.T) {
	minute := time.Date(2026, 8, 27, 11, 23, 0, 0, time.UTC)

	now := minute
	build := func() Derived {
		counters := NewCounters(0)
		for _, service := range []string{"payments", "checkout", "storefront", "cart", "search"} {
			for _, route := range []string{"/a", "/b", "/c"} {
				for index, kind := range []string{KindServer, KindConsumer, "CLIENT"} {
					counters.Observe(now, Span{
						Timestamp: minute.Add(time.Second), ReceivedAt: minute.Add(time.Second),
						TenantID: "t", ProjectID: "p",
						ServiceName: service, Endpoint: route, DurationMS: 5,
						SpanKind: kind, SpanName: route,
						SpanID: []byte(fmt.Sprintf("%s%s%d", service, route, index)),
					})
				}
			}
		}
		return counters.DrainAll()
	}

	first := build()
	if len(first.Points) == 0 || len(first.Endpoints) == 0 {
		t.Fatalf("drained %d points and %d endpoint rows; the fixture is wrong",
			len(first.Points), len(first.Endpoints))
	}

	// Many rounds: a single comparison could pass on a lucky shuffle.
	for round := 0; round < 25; round++ {
		again := build()
		if len(again.Points) != len(first.Points) {
			t.Fatalf("round %d drained %d points, want %d", round, len(again.Points), len(first.Points))
		}
		for i := range first.Points {
			if first.Points[i] != again.Points[i] {
				t.Fatalf("round %d differs at point %d:\n  first: %+v\n  again: %+v",
					round, i, first.Points[i], again.Points[i])
			}
		}

		if len(again.Endpoints) != len(first.Endpoints) {
			t.Fatalf("round %d drained %d endpoint rows, want %d",
				round, len(again.Endpoints), len(first.Endpoints))
		}
		for i := range first.Endpoints {
			if !sameEndpointRow(first.Endpoints[i], again.Endpoints[i]) {
				t.Fatalf("round %d differs at endpoint row %d:\n  first: %+v\n  again: %+v",
					round, i, first.Endpoints[i], again.Endpoints[i])
			}
		}
	}
}

// sameEndpointRow compares two rollup rows field by field, latency sample
// included: a row that agreed on its counts but not on the durations behind
// them would still reach the store as a different block.
func sameEndpointRow(a, b EndpointStat) bool {
	if a.Minute != b.Minute || a.TenantID != b.TenantID || a.ProjectID != b.ProjectID ||
		a.RetentionDays != b.RetentionDays || a.ServiceName != b.ServiceName ||
		a.SpanKind != b.SpanKind || a.SpanName != b.SpanName ||
		a.RequestCount != b.RequestCount || a.ErrorCount != b.ErrorCount ||
		len(a.LatencyMS) != len(b.LatencyMS) {
		return false
	}
	for i := range a.LatencyMS {
		if a.LatencyMS[i] != b.LatencyMS[i] {
			return false
		}
	}
	return true
}
