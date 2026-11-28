package derive

import (
	"cmp"
	"maps"
	"slices"
	"sort"
	"time"
	"unicode"
	"unicode/utf8"
)

// OtherVersion is the version a span is counted under when its own cannot be
// kept: past a cap, too long, or failing the name check. It is a sentinel, never
// a release, and the deploy webhook refuses it as a name (F28 FR9, FR17).
const OtherVersion = "__other__"

// The caps on `service_versions_1m`, which carries free text a customer's SDK
// chose (F28 design section 1, D14). Every value is a starting guess that the
// Step 1a cost report measures. None makes a guarantee about a minute: they bound
// memory and rows, and folding keeps the totals exact.
const (
	// MaxVersionsPerServicePerDrain is how many versions of one service-minute a
	// drain writes, the busiest first; the rest fold into OtherVersion. A minute
	// written in several drains, or by several workers, can still hold more.
	MaxVersionsPerServicePerDrain = 20
	// MaxVersionsPerServiceAtArrival bounds the distinct versions one partition
	// holds for one service-minute between drains. First come, first served.
	MaxVersionsPerServiceAtArrival = 64
	// MaxVersionEntriesPerTenantAtArrival bounds one tenant's service-version
	// entries on one partition and minute, across all its services:
	// MaxServicesPerTenant applies only at drain, so it bounds nothing here.
	MaxVersionEntriesPerTenantAtArrival = 512
	// MaxVersionLength is the Postgres column a version is copied into, in
	// characters. A longer one could never become a release.
	MaxVersionLength = 200
)

// VersionStat is one row of `service_versions_1m`: what one version of one
// service did in one minute. Its counts are the same kind as ServiceStat's, so
// the rows of a service-minute sum to its `service_stats_1m` row.
type VersionStat struct {
	ServiceStat
	Version string
}

// VersionsFolded is how many requests one drain counted under OtherVersion,
// naming the tenant with the most. Every fold lands there — at arrival, at
// drain, or by the name check — so reading the rows is the whole census.
type VersionsFolded struct {
	Requests uint64
	TenantID string
}

// versionKey identifies one row of `service_versions_1m`.
type versionKey struct {
	serviceKey
	version string
}

// countedVersion is one partition's share of one version row, for the reason
// `counted` is: a revoke drops a partition's contribution.
type countedVersion struct {
	versionKey
	partition int32
}

// before orders rows by their service first, so a service-minute's versions are
// one contiguous run for the drain cap.
func (k versionKey) before(other versionKey) bool {
	if k.serviceKey != other.serviceKey {
		return k.serviceKey.before(other.serviceKey)
	}
	return k.version < other.version
}

func (c countedVersion) before(other countedVersion) bool {
	if c.versionKey != other.versionKey {
		return c.versionKey.before(other.versionKey)
	}
	return c.partition < other.partition
}

// tenantArrival is the grain of the per-tenant arrival cap.
type tenantArrival struct {
	minute    time.Time
	tenantID  string
	partition int32
}

// versionArrivals is what the arrival caps have used so far, per partition and
// minute. It is provenance like the shares, freed with them by a drain or a
// revoke.
type versionArrivals struct {
	perService map[countedService]int
	perTenant  map[tenantArrival]int
}

func newVersionArrivals() versionArrivals {
	return versionArrivals{
		perService: make(map[countedService]int),
		perTenant:  make(map[tenantArrival]int),
	}
}

// admit reports whether a new version may have an entry of its own for this
// service-minute on this partition, and takes the room if so.
func (a versionArrivals) admit(service countedService, tenantID string) bool {
	tenant := tenantArrival{
		minute:    service.minute,
		tenantID:  tenantID,
		partition: service.partition,
	}
	if a.perService[service] >= MaxVersionsPerServiceAtArrival ||
		a.perTenant[tenant] >= MaxVersionEntriesPerTenantAtArrival {
		return false
	}
	a.perService[service]++
	a.perTenant[tenant]++
	return true
}

// forget frees the room of every entry the predicate names.
func (a versionArrivals) forget(minuteClosed func(minute time.Time) bool, partitionGone func(int32) bool) {
	for key := range a.perService {
		if minuteClosed(key.minute) || partitionGone(key.partition) {
			delete(a.perService, key)
		}
	}
	for key := range a.perTenant {
		if minuteClosed(key.minute) || partitionGone(key.partition) {
			delete(a.perTenant, key)
		}
	}
}

// NameAllowed is the worker's half of the one name check both doors apply to a
// version and a service name (F28 FR9). A name fails on a control character, a
// bidirectional embedding, override or isolate, a Unicode tag character, or the
// reserved OtherVersion. The webhook refuses such a name; the worker folds it.
// packages/contracts/fixtures/release-names.json holds the cases both read.
func NameAllowed(name string) bool {
	if name == OtherVersion {
		return false
	}
	for _, r := range name {
		if unicode.IsControl(r) || isBidiControl(r) || isTagCharacter(r) {
			return false
		}
	}
	return true
}

// isBidiControl reports the characters that reorder text as it is displayed,
// which can make a name read as something it is not (CVE-2021-42574).
func isBidiControl(r rune) bool {
	return (r >= 0x202A && r <= 0x202E) || (r >= 0x2066 && r <= 0x2069)
}

// isTagCharacter reports the invisible Unicode tag block.
func isTagCharacter(r rune) bool { return r >= 0xE0000 && r <= 0xE007F }

// keptVersion is the version a span counts under before any cap: its own, or
// OtherVersion when it could never become a release.
func keptVersion(version string) string {
	if utf8.RuneCountInString(version) > MaxVersionLength || !NameAllowed(version) {
		return OtherVersion
	}
	return version
}

// observeVersion folds one inbound span into its version row. It is called from
// observeService, so the two tables count exactly the same spans.
func (c *Counters) observeVersion(service countedService, span Span) {
	key := countedVersion{
		versionKey: versionKey{serviceKey: service.serviceKey, version: keptVersion(span.Version)},
		partition:  service.partition,
	}
	totals, ok := c.versions[key]
	if !ok && key.version != OtherVersion && !c.arrivals.admit(service, span.TenantID) {
		key.version = OtherVersion
		totals, ok = c.versions[key]
	}
	if !ok {
		totals = &serviceTotals{}
		c.versions[key] = totals
	}
	totals.requests++
	if span.Errored {
		totals.errors++
	}
	totals.buckets[LatencyBucket(span.DurationMS)]++
}

// drainVersions hands out the version rows for every minute the caller calls
// closed, removing them from memory. Only services in kept are written, so a
// service the per-tenant cap left out leaves its versions out too (FR16).
//
// Rows are folded and sorted deterministically, so a replay drains the same
// block and its deduplication token recognises it.
func (c *Counters) drainVersions(
	closed func(minute time.Time) bool,
	kept map[serviceKey]struct{},
) ([]VersionStat, VersionsFolded) {
	due := make([]countedVersion, 0, len(c.versions))
	for key := range c.versions {
		if closed(key.minute) {
			due = append(due, key)
		}
	}
	sort.Slice(due, func(i, j int) bool { return due[i].before(due[j]) })
	c.arrivals.forget(closed, func(int32) bool { return false })

	var (
		stats   []VersionStat
		run     []VersionStat
		row     versionKey
		running serviceTotals
		open    bool
	)
	flushRow := func() {
		if _, ok := kept[row.serviceKey]; ok {
			run = append(run, row.stat(running))
		}
	}
	for _, key := range due {
		if open && key.versionKey != row {
			flushRow()
			running = serviceTotals{}
			if key.serviceKey != row.serviceKey {
				stats = append(stats, capVersions(run)...)
				run = run[:0]
			}
		}
		row, open = key.versionKey, true
		running.add(c.versions[key])
		delete(c.versions, key)
	}
	if open {
		flushRow()
		stats = append(stats, capVersions(run)...)
	}
	return stats, foldedIn(stats)
}

// capVersions keeps a service-minute's MaxVersionsPerServicePerDrain busiest
// versions, by requests and then by name, and folds the rest into OtherVersion.
// The result is in version order, as the drain sorts.
func capVersions(service []VersionStat) []VersionStat {
	if len(service) == 0 {
		return nil
	}
	var other *VersionStat
	named := make([]VersionStat, 0, len(service))
	for _, stat := range service {
		if stat.Version == OtherVersion {
			folded := stat
			other = &folded
			continue
		}
		named = append(named, stat)
	}
	if len(named) > MaxVersionsPerServicePerDrain {
		slices.SortFunc(named, func(a, b VersionStat) int {
			return cmp.Or(cmp.Compare(b.RequestCount, a.RequestCount), cmp.Compare(a.Version, b.Version))
		})
		if other == nil {
			other = &VersionStat{ServiceStat: service[0].serviceKey().stat(serviceTotals{}), Version: OtherVersion}
		}
		for _, folded := range named[MaxVersionsPerServicePerDrain:] {
			other.fold(folded)
		}
		named = named[:MaxVersionsPerServicePerDrain]
	}
	if other != nil {
		named = append(named, *other)
	}
	slices.SortFunc(named, func(a, b VersionStat) int { return cmp.Compare(a.Version, b.Version) })
	return named
}

// fold adds another version row's counts into this one.
func (s *VersionStat) fold(other VersionStat) {
	s.RequestCount += other.RequestCount
	s.ErrorCount += other.ErrorCount
	for i, count := range other.LatencyBuckets {
		s.LatencyBuckets[i] += count
	}
}

// foldedIn totals what a drain counted under OtherVersion, naming the tenant
// with the most; a tie names the first in name order.
func foldedIn(stats []VersionStat) VersionsFolded {
	byTenant := make(map[string]uint64)
	for _, stat := range stats {
		if stat.Version == OtherVersion {
			byTenant[stat.TenantID] += stat.RequestCount
		}
	}
	var report VersionsFolded
	var most uint64
	for _, tenantID := range slices.Sorted(maps.Keys(byTenant)) {
		report.Requests += byTenant[tenantID]
		if byTenant[tenantID] > most {
			report.TenantID, most = tenantID, byTenant[tenantID]
		}
	}
	return report
}

// stat is the row one version's summed totals become.
func (k versionKey) stat(summed serviceTotals) VersionStat {
	return VersionStat{ServiceStat: k.serviceKey.stat(summed), Version: k.version}
}

// serviceKey is the service-minute a row belongs to.
func (s ServiceStat) serviceKey() serviceKey {
	return serviceKey{
		minute:        s.Minute,
		tenantID:      s.TenantID,
		projectID:     s.ProjectID,
		serviceName:   s.ServiceName,
		retentionDays: s.RetentionDays,
	}
}

// keptServices is the set of service-minutes a drain writes.
func keptServices(stats []ServiceStat) map[serviceKey]struct{} {
	kept := make(map[serviceKey]struct{}, len(stats))
	for _, stat := range stats {
		kept[stat.serviceKey()] = struct{}{}
	}
	return kept
}

// PendingVersions reports how many version-minute shares are held. Logged beside
// PendingServices because it is the map a customer's version text can grow,
// bounded by the arrival caps.
func (c *Counters) PendingVersions() int { return len(c.versions) }
