package derive

import (
	"fmt"
	"math"
	"math/rand/v2"
	"testing"
	"time"
)

var serviceMinute = time.Date(2026, 8, 27, 11, 23, 0, 0, time.UTC)

// inboundSpan is one request served by a service, on a partition.
func inboundSpan(partition int32, tenant, service string, durationMS float64, errored bool) Span {
	at := serviceMinute.Add(10 * time.Second)
	return Span{
		TenantID: tenant, ProjectID: "project-1", ServiceName: service,
		Endpoint: "/checkout", SpanKind: KindServer, SpanName: "GET /checkout",
		Timestamp: at, ReceivedAt: at, DurationMS: durationMS, Errored: errored,
		Partition: partition, RetentionDays: 30,
	}
}

func drainedServices(t *testing.T, counters *Counters) []ServiceStat {
	t.Helper()
	return counters.DrainAll().Services
}

// Detection judges a count above a boundary and a p95 from these buckets, so
// every inbound span has to be in exactly one of them — before sampling, and
// whatever its duration.
func TestEveryInboundSpanIsCountedInItsLatencyBucket(t *testing.T) {
	counters := NewCounters(0)
	durations := []float64{0.2, 1, 2, 100, 59_000, 120_000}
	for index, ms := range durations {
		counters.Observe(serviceMinute, inboundSpan(0, "tenant-1", "checkout", ms, index%2 == 0))
	}
	consumer := inboundSpan(0, "tenant-1", "checkout", 5, false)
	consumer.SpanKind = KindConsumer
	counters.Observe(serviceMinute, consumer)
	// An outbound call is the callee's request, not this service's.
	client := inboundSpan(0, "tenant-1", "checkout", 5, true)
	client.SpanKind = "CLIENT"
	counters.Observe(serviceMinute, client)

	stats := drainedServices(t, counters)
	if len(stats) != 1 {
		t.Fatalf("drained %d service rows, want one: %+v", len(stats), stats)
	}
	stat := stats[0]
	if stat.RequestCount != 7 || stat.ErrorCount != 3 {
		t.Errorf("requests %d, errors %d; want 7 and 3 — SERVER and CONSUMER only",
			stat.RequestCount, stat.ErrorCount)
	}
	var bucketed uint64
	for _, count := range stat.LatencyBuckets {
		bucketed += uint64(count)
	}
	if bucketed != stat.RequestCount {
		t.Errorf("the buckets hold %d requests, want every one of %d", bucketed, stat.RequestCount)
	}
	for _, want := range []struct {
		ms     float64
		bucket int
	}{{0.2, 0}, {1, 0}, {100, LatencyBucket(100)}, {59_000, LatencyBucketCount - 1}, {120_000, LatencyBucketCount - 1}} {
		if stat.LatencyBuckets[want.bucket] == 0 {
			t.Errorf("%v ms was not counted in bucket %d", want.ms, want.bucket)
		}
	}
}

// The boundaries are the simulation's (tools/detection-sim LOG_EDGES, 65
// log-spaced edges from 1 ms to 60 s), or the method the scheduler ports from it
// reads a different histogram from the one it was tuned on.
func TestTheLatencyBoundariesAreTheSimulationsEdges(t *testing.T) {
	for i, bound := range latencyBucketBounds {
		want := math.Pow(60_000, float64(i+1)/64)
		if math.Abs(bound-want) > want*1e-12 {
			t.Fatalf("bound %d = %v, want %v", i, bound, want)
		}
		if got := LatencyBucket(bound); got != i {
			t.Errorf("a duration on bound %d is in bucket %d; a bucket includes its upper bound", i, got)
		}
		if got := LatencyBucket(math.Nextafter(bound, math.Inf(1))); got != i+1 {
			t.Errorf("a duration just above bound %d is in bucket %d, want %d", i, got, i+1)
		}
	}
	for _, broken := range []float64{math.NaN(), -3, 0} {
		if got := LatencyBucket(broken); got != 0 {
			t.Errorf("LatencyBucket(%v) = %d, want the first bucket", broken, got)
		}
	}
}

// A partition the group took back is counted by its new owner, so its share
// leaves here, as it does from the other rollups.
func TestARevokedPartitionsShareLeavesTheServiceRollup(t *testing.T) {
	counters := NewCounters(0)
	for range 4 {
		counters.Observe(serviceMinute, inboundSpan(1, "tenant-1", "checkout", 10, true))
	}
	for range 6 {
		counters.Observe(serviceMinute, inboundSpan(2, "tenant-1", "checkout", 10, true))
	}

	if dropped := counters.Disown([]int32{2}); dropped.Services != 1 {
		t.Errorf("Disown reported %d service shares, want 1", dropped.Services)
	}

	stats := drainedServices(t, counters)
	if len(stats) != 1 || stats[0].RequestCount != 4 || stats[0].ErrorCount != 4 {
		t.Fatalf("drained %+v, want one row of 4 requests — only the partition still owned", stats)
	}
	if bucket := stats[0].LatencyBuckets[LatencyBucket(10)]; bucket != 4 {
		t.Errorf("the 10 ms bucket holds %d, want 4", bucket)
	}
}

// The insert is deduplicated by a token over the drained rows, so the same
// offsets re-derived must drain the same block, however records from different
// partitions interleaved the second time.
func TestTheServiceDrainIsByteReproducible(t *testing.T) {
	type observation struct {
		partition  int32
		service    string
		durationMS float64
	}
	var observations []observation
	for partition := range int32(4) {
		for _, service := range []string{"payments", "checkout", "storefront", "cart", "search"} {
			for step := range 5 {
				observations = append(observations,
					observation{partition, service, float64(step*37+int(partition)) + 0.5})
			}
		}
	}

	build := func(seed uint64) []ServiceStat {
		shuffled := append([]observation(nil), observations...)
		rand.New(rand.NewPCG(seed, seed)).Shuffle(len(shuffled), func(i, j int) {
			shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
		})
		counters := NewCounters(0)
		for _, seen := range shuffled {
			counters.Observe(serviceMinute,
				inboundSpan(seen.partition, "tenant-1", seen.service, seen.durationMS, false))
		}
		return drainedServices(t, counters)
	}

	first := build(1)
	if len(first) != 5 {
		t.Fatalf("drained %d service rows, want 5; the fixture is wrong", len(first))
	}
	for round := uint64(2); round < 27; round++ {
		again := build(round)
		if len(again) != len(first) {
			t.Fatalf("round %d drained %d rows, want %d", round, len(again), len(first))
		}
		for i := range first {
			if again[i] != first[i] {
				t.Fatalf("round %d differs at row %d:\n  first: %+v\n  again: %+v", round, i, first[i], again[i])
			}
		}
		if serviceDeduplicationToken(again) != serviceDeduplicationToken(first) {
			t.Fatalf("round %d drained the same rows under a different token", round)
		}
	}
}

// One organization can have at most 2,000 services counted in a minute. Past
// that, the rest of its services are left out and reported, and nobody else's
// are affected.
func TestTheCapLeavesOutAnOrganizationsServicesPastTwoThousand(t *testing.T) {
	const extra = 5
	counters := NewCounters(0)
	for index := range MaxServicesPerTenant + extra {
		counters.Observe(serviceMinute,
			inboundSpan(int32(index%3), "tenant-noisy", fmt.Sprintf("svc-%05d", index), 5, false))
	}
	counters.Observe(serviceMinute, inboundSpan(0, "tenant-quiet", "checkout", 5, false))
	nextMinute := inboundSpan(0, "tenant-noisy", "svc-99999", 5, false)
	nextMinute.Timestamp = serviceMinute.Add(time.Minute)
	nextMinute.ReceivedAt = nextMinute.Timestamp
	counters.Observe(serviceMinute, nextMinute)

	drained := counters.DrainAll()

	kept := make(map[string]int)
	for _, stat := range drained.Services {
		if stat.Minute.Equal(serviceMinute) {
			kept[stat.TenantID]++
		}
		if stat.ServiceName >= fmt.Sprintf("svc-%05d", MaxServicesPerTenant) && stat.Minute.Equal(serviceMinute) {
			t.Errorf("%s was counted past the cap; the ones left out are the last in name order",
				stat.ServiceName)
		}
	}
	if kept["tenant-noisy"] != MaxServicesPerTenant {
		t.Errorf("tenant-noisy has %d services in the minute, want the cap of %d",
			kept["tenant-noisy"], MaxServicesPerTenant)
	}
	if kept["tenant-quiet"] != 1 {
		t.Errorf("tenant-quiet has %d services, want 1: the cap is per organization", kept["tenant-quiet"])
	}
	if len(drained.Services) != MaxServicesPerTenant+2 {
		t.Errorf("drained %d rows, want the capped minute, the quiet tenant and the next minute",
			len(drained.Services))
	}
	if want := (ServiceCap{Services: extra, TenantID: "tenant-noisy"}); drained.ServicesOverCap != want {
		t.Errorf("ServicesOverCap = %+v, want %+v", drained.ServicesOverCap, want)
	}
}
