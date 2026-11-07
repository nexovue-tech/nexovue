package derive

import (
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"os"
	"strings"
	"testing"
	"time"
)

// versionedSpan is one request served by a service at a version, on a partition.
func versionedSpan(partition int32, service, version string, durationMS float64, errored bool) Span {
	built := inboundSpan(partition, "tenant-1", service, durationMS, errored)
	built.Version = version
	return built
}

// versionRequests is how many requests each version row of one service holds.
func versionRequests(rows []VersionStat, service string) map[string]uint64 {
	found := make(map[string]uint64)
	for _, row := range rows {
		if row.ServiceName == service {
			found[row.Version] += row.RequestCount
		}
	}
	return found
}

// sumsMatch holds AC15: for every service-minute, the version rows add up to the
// service row — requests, errors and every latency bucket.
func sumsMatch(t *testing.T, drained Derived) {
	t.Helper()
	type summed struct {
		requests, errors uint64
		buckets          [LatencyBucketCount]uint64
	}
	fromVersions := make(map[serviceKey]*summed)
	for _, row := range drained.Versions {
		key := row.serviceKey()
		total, ok := fromVersions[key]
		if !ok {
			total = &summed{}
			fromVersions[key] = total
		}
		total.requests += row.RequestCount
		total.errors += row.ErrorCount
		for i, count := range row.LatencyBuckets {
			total.buckets[i] += uint64(count)
		}
	}
	if len(fromVersions) != len(drained.Services) {
		t.Fatalf("version rows cover %d service-minutes, service rows %d",
			len(fromVersions), len(drained.Services))
	}
	for _, service := range drained.Services {
		total, ok := fromVersions[service.serviceKey()]
		if !ok {
			t.Fatalf("service %s at %s has no version rows", service.ServiceName, service.Minute)
		}
		if total.requests != service.RequestCount || total.errors != service.ErrorCount {
			t.Errorf("%s: versions sum to %d requests and %d errors, the service row holds %d and %d",
				service.ServiceName, total.requests, total.errors, service.RequestCount, service.ErrorCount)
		}
		for i, count := range service.LatencyBuckets {
			if total.buckets[i] != uint64(count) {
				t.Errorf("%s: bucket %d sums to %d across versions, the service row holds %d",
					service.ServiceName, i, total.buckets[i], count)
			}
		}
	}
}

// AC15 for any mix: random services, versions, partitions, kinds, durations and
// failures, including names that fold, over many seeds.
func TestVersionRowsSumToTheServiceRowForAnyMix(t *testing.T) {
	versions := []string{"", "v1", "v2", "v3", "__other__", strings.Repeat("x", 201), "bad\u202e"}
	kinds := []string{KindServer, KindConsumer, "CLIENT", "INTERNAL"}
	for seed := uint64(1); seed <= 40; seed++ {
		rng := rand.New(rand.NewPCG(seed, seed))
		counters := NewCounters(0)
		for range 3000 {
			built := versionedSpan(rng.Int32N(6), fmt.Sprintf("svc-%d", rng.IntN(4)),
				versions[rng.IntN(len(versions))], rng.Float64()*2000, rng.IntN(7) == 0)
			built.SpanKind = kinds[rng.IntN(len(kinds))]
			built.Timestamp = serviceMinute.Add(time.Duration(rng.IntN(180)) * time.Second)
			built.ReceivedAt = built.Timestamp
			counters.Observe(serviceMinute, built)
		}
		drained := counters.DrainAll()
		if len(drained.Versions) == 0 {
			t.Fatalf("seed %d drained no version rows; the fixture is wrong", seed)
		}
		sumsMatch(t, drained)
	}
}

// AC18: 25 versions in one drain become the 20 busiest and one `__other__`, and
// the totals do not move.
func TestTwentyFiveVersionsInOneDrainKeepTheTwentyBusiest(t *testing.T) {
	counters := NewCounters(0)
	for index := range 25 {
		// Version v00 is the busiest, v24 the quietest.
		for range 30 - index {
			counters.Observe(serviceMinute,
				versionedSpan(int32(index%3), "checkout", fmt.Sprintf("v%02d", index), 5, false))
		}
	}

	drained := counters.DrainAll()
	got := versionRequests(drained.Versions, "checkout")

	if len(drained.Versions) != MaxVersionsPerServicePerDrain+1 {
		t.Fatalf("drained %d version rows, want %d and one %s: %v",
			len(drained.Versions), MaxVersionsPerServicePerDrain, OtherVersion, got)
	}
	for index := range MaxVersionsPerServicePerDrain {
		if version := fmt.Sprintf("v%02d", index); got[version] != uint64(30-index) {
			t.Errorf("%s holds %d requests, want %d", version, got[version], 30-index)
		}
	}
	// v20 to v24: 10 + 9 + 8 + 7 + 6.
	if got[OtherVersion] != 40 {
		t.Errorf("%s holds %d requests, want the five quietest versions' 40", OtherVersion, got[OtherVersion])
	}
	sumsMatch(t, drained)
}

// A tie in requests is broken by name, so the same counts always keep the same
// versions and a replay drains the same block.
func TestTheDrainCapBreaksATieByName(t *testing.T) {
	counters := NewCounters(0)
	for index := range MaxVersionsPerServicePerDrain + 1 {
		counters.Observe(serviceMinute,
			versionedSpan(0, "checkout", fmt.Sprintf("v%02d", MaxVersionsPerServicePerDrain-index), 5, false))
	}

	got := versionRequests(counters.DrainAll().Versions, "checkout")
	last := fmt.Sprintf("v%02d", MaxVersionsPerServicePerDrain)
	if _, kept := got[last]; kept {
		t.Errorf("%s was kept; with equal counts the last name in order is the one folded", last)
	}
	if got[OtherVersion] != 1 {
		t.Errorf("%s holds %d requests, want 1", OtherVersion, got[OtherVersion])
	}
}

// AC18: the same version split across two drains that fold differently. Totals
// across both stay exact, and the version's own count comes out short, which
// `__other__` holding requests is what tells a reader.
func TestAVersionFoldedInOneDrainAndKeptInAnotherKeepsTotalsExact(t *testing.T) {
	first, second := NewCounters(0), NewCounters(0)
	// In the first drain v-real is the quietest of 21 and is folded.
	for index := range MaxVersionsPerServicePerDrain {
		for range 5 {
			first.Observe(serviceMinute, versionedSpan(0, "checkout", fmt.Sprintf("noise-%02d", index), 5, false))
		}
	}
	first.Observe(serviceMinute, versionedSpan(0, "checkout", "v-real", 5, false))
	// In the second, written by another worker for the same minute, it is alone.
	for range 7 {
		second.Observe(serviceMinute, versionedSpan(1, "checkout", "v-real", 5, false))
	}

	one, two := first.DrainAll(), second.DrainAll()
	sumsMatch(t, one)
	sumsMatch(t, two)

	stored := versionRequests(append(one.Versions, two.Versions...), "checkout")
	services := one.Services[0].RequestCount + two.Services[0].RequestCount
	var total uint64
	for _, requests := range stored {
		total += requests
	}
	if total != services {
		t.Errorf("both drains together hold %d version requests, the service rows %d", total, services)
	}
	if stored["v-real"] != 7 || stored[OtherVersion] != 1 {
		t.Errorf("v-real = %d and %s = %d, want 7 and 1: one drain kept it and one folded it",
			stored["v-real"], OtherVersion, stored[OtherVersion])
	}
}

// AC18: arrival is first come, first served. 64 junk names early in a minute,
// on one partition and service, fill the arrival cap, and the dominant real
// version that follows counts wholly under `__other__`. Totals stay exact.
func TestAJunkBurstBeforeTheRealVersionAbsorbsItsWholeCount(t *testing.T) {
	counters := NewCounters(0)
	for index := range MaxVersionsPerServiceAtArrival {
		counters.Observe(serviceMinute, versionedSpan(0, "checkout", fmt.Sprintf("junk-%02d", index), 5, false))
	}
	for range 500 {
		counters.Observe(serviceMinute, versionedSpan(0, "checkout", "v2.1", 5, true))
	}

	drained := counters.DrainAll()
	got := versionRequests(drained.Versions, "checkout")

	if _, ok := got["v2.1"]; ok {
		t.Errorf("v2.1 has a row of its own (%d requests); past the arrival cap it must fold", got["v2.1"])
	}
	// 500 real requests, plus the 44 junk names the drain cap folds on top.
	if want := uint64(500 + MaxVersionsPerServiceAtArrival - MaxVersionsPerServicePerDrain); got[OtherVersion] != want {
		t.Errorf("%s holds %d requests, want %d", OtherVersion, got[OtherVersion], want)
	}
	if drained.VersionsFolded.Requests == 0 {
		t.Error("the fold was not reported, so nothing would say versions were combined")
	}
	sumsMatch(t, drained)
}

// The arrival cap is per partition: a partition's order is fixed by its offsets,
// so the same records always fold the same way, and another partition's names
// do not use up this one's room.
func TestTheArrivalCapIsCountedPerPartition(t *testing.T) {
	counters := NewCounters(0)
	for index := range MaxVersionsPerServiceAtArrival {
		counters.Observe(serviceMinute, versionedSpan(0, "checkout", fmt.Sprintf("junk-%02d", index), 5, false))
	}
	for range 500 {
		counters.Observe(serviceMinute, versionedSpan(1, "checkout", "v2.1", 5, false))
	}

	if got := versionRequests(counters.DrainAll().Versions, "checkout"); got["v2.1"] != 500 {
		t.Errorf("v2.1 holds %d requests, want all 500: it arrived on another partition", got["v2.1"])
	}
}

// One tenant's many services are bounded together at arrival: past 512 distinct
// service-version entries on a partition and minute, a new version counts under
// its own service's `__other__`.
func TestTheTenantArrivalCapBoundsManyServicesTogether(t *testing.T) {
	counters := NewCounters(0)
	for service := range MaxVersionEntriesPerTenantAtArrival / 8 {
		for version := range 8 {
			counters.Observe(serviceMinute, versionedSpan(0, fmt.Sprintf("svc-%03d", service),
				fmt.Sprintf("v%d", version), 5, false))
		}
	}
	counters.Observe(serviceMinute, versionedSpan(0, "late-service", "v1", 5, false))

	drained := counters.DrainAll()
	got := versionRequests(drained.Versions, "late-service")
	if got[OtherVersion] != 1 || len(got) != 1 {
		t.Errorf("late-service drained %v, want its one request under %s: the tenant is at its cap",
			got, OtherVersion)
	}
	sumsMatch(t, drained)
}

// FR17: a version over 200 characters, or failing the shared name check, folds.
func TestLongAndControlCharacterVersionsFold(t *testing.T) {
	cases := map[string]string{
		"201 characters":     strings.Repeat("a", MaxVersionLength+1),
		"a newline":          "v1\nv2",
		"NUL":                "v1\x00",
		"a bidi override":    "v1\u202e",
		"a tag character":    "v1\U000E0041",
		"the reserved value": OtherVersion,
	}
	for name, version := range cases {
		t.Run(name, func(t *testing.T) {
			counters := NewCounters(0)
			counters.Observe(serviceMinute, versionedSpan(0, "checkout", version, 5, false))

			drained := counters.DrainAll()
			got := versionRequests(drained.Versions, "checkout")
			if got[OtherVersion] != 1 || len(got) != 1 {
				t.Errorf("drained %v, want the request under %s", got, OtherVersion)
			}
			sumsMatch(t, drained)
		})
	}

	// Exactly 200 characters fits the Postgres column, in characters not bytes.
	counters := NewCounters(0)
	fits := strings.Repeat("é", MaxVersionLength)
	counters.Observe(serviceMinute, versionedSpan(0, "checkout", fits, 5, false))
	if got := versionRequests(counters.DrainAll().Versions, "checkout"); got[fits] != 1 {
		t.Errorf("a 200-character version was folded: %v", got)
	}
}

// The name check agrees with the shared cases the webhook's check will read too.
func TestTheNameCheckAgreesWithTheSharedCases(t *testing.T) {
	raw, err := os.ReadFile("../../../../../packages/contracts/fixtures/release-names.json")
	if err != nil {
		t.Fatalf("reading the shared cases: %v", err)
	}
	var fixture struct {
		Cases []struct {
			Name    string `json:"name"`
			Value   string `json:"value"`
			Allowed bool   `json:"allowed"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("decoding the shared cases: %v", err)
	}
	if len(fixture.Cases) == 0 {
		t.Fatal("the shared fixture holds no cases")
	}
	for _, tc := range fixture.Cases {
		if got := NameAllowed(tc.Value); got != tc.Allowed {
			t.Errorf("%s: NameAllowed(%q) = %v, want %v", tc.Name, tc.Value, got, tc.Allowed)
		}
	}
}

// FR16: a service the per-tenant cap leaves out leaves its version rows out in
// the same drain, or the versions would sum to more than the service table.
func TestAServiceLeftOutByTheServiceCapLeavesItsVersionsOut(t *testing.T) {
	counters := NewCounters(0)
	for index := range MaxServicesPerTenant + 3 {
		built := versionedSpan(int32(index%3), fmt.Sprintf("svc-%05d", index), "v1", 5, false)
		built.TenantID = "tenant-noisy"
		counters.Observe(serviceMinute, built)
	}

	drained := counters.DrainAll()
	if len(drained.Versions) != MaxServicesPerTenant {
		t.Errorf("drained %d version rows, want one per kept service (%d)",
			len(drained.Versions), MaxServicesPerTenant)
	}
	sumsMatch(t, drained)
}

// A browser-door span reaches the counters with no version until the browser-door
// change keeps it out of both tables; until then it counts under the empty one.
func TestASpanWithNoVersionCountsUnderTheEmptyVersion(t *testing.T) {
	counters := NewCounters(0)
	counters.Observe(serviceMinute, versionedSpan(0, "checkout", "", 5, false))
	counters.Observe(serviceMinute, versionedSpan(0, "checkout", "v1", 5, false))

	got := versionRequests(counters.DrainAll().Versions, "checkout")
	if got[""] != 1 || got["v1"] != 1 {
		t.Errorf("drained %v, want one request under the empty version and one under v1", got)
	}
}

// AC16: the version rows of a replay are byte-identical whatever order records
// of different partitions interleaved in, so their token recognises the retry.
func TestVersionDrainIsReproducibleAcrossInterleavings(t *testing.T) {
	type seen struct {
		partition  int32
		service    string
		version    string
		durationMS float64
	}
	var records []seen
	for index := range 300 {
		records = append(records, seen{
			partition:  int32(index % 4),
			service:    fmt.Sprintf("svc-%d", index%3),
			version:    fmt.Sprintf("v%d", index%30),
			durationMS: float64(index%17) * 3.5,
		})
	}
	build := func(seed uint64) []VersionStat {
		// Shuffle across partitions while keeping each partition's own order, which
		// is all a replay preserves.
		byPartition := make(map[int32][]seen)
		for _, record := range records {
			byPartition[record.partition] = append(byPartition[record.partition], record)
		}
		rng := rand.New(rand.NewPCG(seed, seed))
		counters := NewCounters(0)
		for len(byPartition) > 0 {
			partition := int32(rng.IntN(4))
			queue := byPartition[partition]
			if len(queue) == 0 {
				delete(byPartition, partition)
				continue
			}
			next := queue[0]
			byPartition[partition] = queue[1:]
			counters.Observe(serviceMinute,
				versionedSpan(next.partition, next.service, next.version, next.durationMS, false))
		}
		return counters.DrainAll().Versions
	}

	first := build(1)
	for round := uint64(2); round < 20; round++ {
		again := build(round)
		if len(again) != len(first) {
			t.Fatalf("round %d drained %d rows, want %d", round, len(again), len(first))
		}
		for i := range first {
			if again[i] != first[i] {
				t.Fatalf("round %d differs at row %d:\n  first: %+v\n  again: %+v", round, i, first[i], again[i])
			}
		}
		if versionDeduplicationToken(again) != versionDeduplicationToken(first) {
			t.Fatalf("round %d drained the same rows under a different token", round)
		}
	}
}

// A partition handed to another worker takes its version shares with it, and
// the arrival room they used, so the partition coming back is not folded by
// counts this worker no longer holds.
func TestARevokedPartitionsVersionSharesAndArrivalRoomLeave(t *testing.T) {
	counters := NewCounters(0)
	for index := range MaxVersionsPerServiceAtArrival {
		counters.Observe(serviceMinute, versionedSpan(2, "checkout", fmt.Sprintf("junk-%02d", index), 5, false))
	}
	for range 4 {
		counters.Observe(serviceMinute, versionedSpan(1, "checkout", "v1", 5, false))
	}

	if dropped := counters.Disown([]int32{2}); dropped.Versions != MaxVersionsPerServiceAtArrival {
		t.Errorf("Disown dropped %d version shares, want %d", dropped.Versions, MaxVersionsPerServiceAtArrival)
	}
	if counters.PendingVersions() != 1 {
		t.Errorf("PendingVersions = %d after the revoke, want 1", counters.PendingVersions())
	}
	counters.Observe(serviceMinute, versionedSpan(2, "checkout", "v2", 5, false))

	drained := counters.DrainAll()
	got := versionRequests(drained.Versions, "checkout")
	if got["v1"] != 4 || got["v2"] != 1 || len(got) != 2 {
		t.Errorf("drained %v, want v1 = 4 and v2 = 1: the revoked partition's room went with it", got)
	}
	sumsMatch(t, drained)
	if counters.PendingVersions() != 0 {
		t.Errorf("PendingVersions = %d after the drain, want 0", counters.PendingVersions())
	}
}

// The arrival room is freed with its minute, so a long-running worker does not
// keep folding a service whose earlier minutes were busy.
func TestArrivalRoomIsFreedWhenItsMinuteDrains(t *testing.T) {
	counters := NewCounters(0)
	for index := range MaxVersionsPerServiceAtArrival {
		counters.Observe(serviceMinute, versionedSpan(0, "checkout", fmt.Sprintf("junk-%02d", index), 5, false))
	}
	counters.Drain(serviceMinute.Add(2 * time.Minute))

	next := versionedSpan(0, "checkout", "v2.1", 5, false)
	next.Timestamp = serviceMinute.Add(time.Minute + 10*time.Second)
	next.ReceivedAt = next.Timestamp
	counters.Observe(serviceMinute, next)
	if got := versionRequests(counters.DrainAll().Versions, "checkout"); got["v2.1"] != 1 {
		t.Errorf("drained %v, want v2.1 kept: the earlier minute's room should be gone", got)
	}
}
