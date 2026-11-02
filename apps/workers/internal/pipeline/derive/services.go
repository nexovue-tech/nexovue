package derive

import (
	"maps"
	"math"
	"slices"
	"sort"
	"time"
)

// LatencyBucketCount is how many latency buckets a `service_stats_1m` row
// carries. The boundaries are fixed rather than fitted to each service, so rows
// from any worker, minute or service add bucket by bucket.
const LatencyBucketCount = 64

// The range the buckets span: 1 ms to 60 s, log-spaced, so every bucket is the
// same ratio wide (about 19%). A duration outside it folds into the end bucket
// on its side, as anomaly detection's simulation does (tools/detection-sim,
// LOG_EDGES), so the two agree on every count.
const (
	latencyFloorMS   = 1.0
	latencyCeilingMS = 60_000.0
)

// latencyBucketBounds are the 63 boundaries between the 64 buckets. Bucket i
// holds durations above bound i-1 and at most bound i; bucket 0 has no lower
// bound and bucket 63 no upper one.
var latencyBucketBounds = latencyBounds()

// latencyBounds places boundary i at floor × (ceiling/floor)^((i+1)/64): the
// inner edges of 65 log-spaced edges from the floor to the ceiling.
func latencyBounds() [LatencyBucketCount - 1]float64 {
	var bounds [LatencyBucketCount - 1]float64
	for i := range bounds {
		bounds[i] = latencyFloorMS *
			math.Pow(latencyCeilingMS/latencyFloorMS, float64(i+1)/LatencyBucketCount)
	}
	return bounds
}

// LatencyBucket is the bucket a duration in milliseconds is counted in. A
// duration that is not a number or not positive is counted in the first: a
// broken clock is still a request.
func LatencyBucket(durationMS float64) int {
	if math.IsNaN(durationMS) || durationMS <= 0 {
		return 0
	}
	return sort.SearchFloat64s(latencyBucketBounds[:], durationMS)
}

// MaxServicesPerTenant bounds how many services one organization may have
// counted in one minute (design 2.1, security M5). Past it, the rest of that
// tenant's services are not written for that minute: one customer minting
// service names cannot grow the rollup, or detection's work, without limit.
const MaxServicesPerTenant = 2000

// ServiceStat is one row of `service_stats_1m`: what one service did in one
// minute, over every inbound span rather than the sampled ones.
type ServiceStat struct {
	Minute        time.Time
	TenantID      string
	ProjectID     string
	RetentionDays uint16
	ServiceName   string
	RequestCount  uint64
	ErrorCount    uint64
	// LatencyBuckets counts the requests in each fixed bucket (LatencyBucket).
	// uint32 because one service-minute on one worker cannot reach 4 billion
	// requests; the store sums them as UInt64.
	LatencyBuckets [LatencyBucketCount]uint32
}

// serviceKey identifies one row of `service_stats_1m`: the table's sort key
// plus the retention its partition key carries.
type serviceKey struct {
	minute        time.Time
	tenantID      string
	projectID     string
	serviceName   string
	retentionDays uint16
}

// countedService is one partition's share of one service-minute, for the reason
// `counted` is: a revoke drops a partition's contribution.
type countedService struct {
	serviceKey
	partition int32
}

// before orders shares so the ones of a service-minute are adjacent and in
// ascending partition order, which keeps a drain reproducible.
func (c countedService) before(other countedService) bool {
	if c.serviceKey != other.serviceKey {
		return c.serviceKey.before(other.serviceKey)
	}
	return c.partition < other.partition
}

// before orders rows by minute and tenant first, which is the grain the cap is
// counted at, so one tenant's minute is one contiguous run.
func (k serviceKey) before(other serviceKey) bool {
	switch {
	case !k.minute.Equal(other.minute):
		return k.minute.Before(other.minute)
	case k.tenantID != other.tenantID:
		return k.tenantID < other.tenantID
	case k.projectID != other.projectID:
		return k.projectID < other.projectID
	case k.serviceName != other.serviceName:
		return k.serviceName < other.serviceName
	default:
		return k.retentionDays < other.retentionDays
	}
}

type serviceTotals struct {
	requests uint64
	errors   uint64
	buckets  [LatencyBucketCount]uint32
}

// add folds one partition's share into a running total for its service-minute.
func (t *serviceTotals) add(share *serviceTotals) {
	t.requests += share.requests
	t.errors += share.errors
	for i, count := range share.buckets {
		t.buckets[i] += count
	}
}

// ServiceCap is what the per-tenant cap left out of one drain. It names one
// tenant, the one that lost the most, because the log line it becomes is a
// pointer to go and look rather than a census.
type ServiceCap struct {
	Services int
	TenantID string
}

// observeService folds one inbound span into its service-minute. Its caller has
// already applied the endpoint rollup's inbound filter, so the two rollups
// count exactly the same spans.
func (c *Counters) observeService(minute time.Time, span Span) {
	key := countedService{
		serviceKey: serviceKey{
			minute:        minute,
			tenantID:      span.TenantID,
			projectID:     span.ProjectID,
			serviceName:   span.ServiceName,
			retentionDays: span.RetentionDays,
		},
		partition: span.Partition,
	}
	totals, ok := c.services[key]
	if !ok {
		totals = &serviceTotals{}
		c.services[key] = totals
	}
	totals.requests++
	if span.Errored {
		totals.errors++
	}
	totals.buckets[LatencyBucket(span.DurationMS)]++
}

// drainServices hands out the rows for every minute the caller calls closed,
// removing them from memory, and applies the per-tenant cap.
//
// The cap is applied here rather than as spans arrive, and to rows in sorted
// order, so which services are left out does not depend on how records from
// different partitions interleaved. A replay therefore drains the same block,
// and its deduplication token still recognises it.
func (c *Counters) drainServices(closed func(minute time.Time) bool) ([]ServiceStat, ServiceCap) {
	due := make([]countedService, 0, len(c.services))
	for key := range c.services {
		if closed(key.minute) {
			due = append(due, key)
		}
	}
	sort.Slice(due, func(i, j int) bool { return due[i].before(due[j]) })

	stats := make([]ServiceStat, 0, len(due))
	var (
		row     serviceKey
		running serviceTotals
		open    bool
	)
	for _, key := range due {
		if open && key.serviceKey != row {
			stats = append(stats, row.stat(running))
			running = serviceTotals{}
		}
		row, open = key.serviceKey, true
		running.add(c.services[key])
		delete(c.services, key)
	}
	if open {
		stats = append(stats, row.stat(running))
	}
	return capServices(stats)
}

// capServices keeps at most MaxServicesPerTenant rows per tenant and minute,
// in the order given. A service in two projects is two services here, because
// detection judges each one.
func capServices(sorted []ServiceStat) ([]ServiceStat, ServiceCap) {
	kept := sorted[:0]
	dropped := make(map[string]int)
	var (
		minute  time.Time
		tenant  string
		counted int
	)
	for _, stat := range sorted {
		if !stat.Minute.Equal(minute) || stat.TenantID != tenant {
			minute, tenant, counted = stat.Minute, stat.TenantID, 0
		}
		counted++
		if counted > MaxServicesPerTenant {
			dropped[stat.TenantID]++
			continue
		}
		kept = append(kept, stat)
	}

	var (
		report ServiceCap
		most   int
	)
	// In name order, so a tie names the same tenant every time.
	for _, tenantID := range slices.Sorted(maps.Keys(dropped)) {
		report.Services += dropped[tenantID]
		if dropped[tenantID] > most {
			report.TenantID, most = tenantID, dropped[tenantID]
		}
	}
	return kept, report
}

// stat is the row one service-minute's summed totals become.
func (k serviceKey) stat(summed serviceTotals) ServiceStat {
	return ServiceStat{
		Minute:         k.minute,
		TenantID:       k.tenantID,
		ProjectID:      k.projectID,
		RetentionDays:  k.retentionDays,
		ServiceName:    k.serviceName,
		RequestCount:   summed.requests,
		ErrorCount:     summed.errors,
		LatencyBuckets: summed.buckets,
	}
}
