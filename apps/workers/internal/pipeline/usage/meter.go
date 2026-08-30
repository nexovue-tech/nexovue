package usage

import (
	"sort"
	"time"
)

// A day is a day in UTC. Every tenant's usage day therefore starts at the same
// instant, whatever timezone they read it in, and the boundary is our own clock
// rather than a client's — the same choice storage partitions make (ADR 0067),
// for the same reason: a wrong clock on a customer's host must not be able to
// move a number we bill on.
const day = 24 * time.Hour

// tenantDay is one row of `usage_daily`: an organization, a project, and a UTC
// calendar day. Comparable, so it keys a map without a formatted string per
// message.
type tenantDay struct {
	orgID     string
	projectID string
	day       time.Time
}

// Row is one tenant-day, ready to be written: the counters to add, and the
// services to add to the day's set.
type Row struct {
	OrgID     string
	ProjectID string
	Day       time.Time
	Counts

	// Services is sorted and distinct. Sorted for the same reason the rows are:
	// one flush takes its locks in a stable order.
	Services []string
}

// bucket is what one tenant-day has accumulated since the last flush: counters
// to add, and services to union into the day's set.
//
// The services are a set here and a set in Postgres, which is the whole reason
// the billing unit survives a replay: adding a name twice leaves it unchanged,
// where adding a count twice does not.
type bucket struct {
	counts   Counts
	services map[string]struct{}
}

// Meter accumulates counters in memory and hands them out in flushes, the way
// derive accumulates per-minute counters: a write per message would put a
// Postgres round trip in the path of every ingested batch.
//
// It is not safe for concurrent use; one pipeline goroutine owns it.
type Meter struct {
	totals map[tenantDay]*bucket
}

// NewMeter returns an empty meter.
func NewMeter() *Meter {
	return &Meter{totals: make(map[tenantDay]*bucket)}
}

// Observe adds one message to the day it was received on: its counts to the
// day's counters, its services to the day's set.
//
// receivedAt is our own clock, taken from the envelope the receiver stamped. A
// message carrying nothing — an empty export, which SDKs do send — is dropped
// here rather than opening a bucket that would flush a row of zeros.
func (m *Meter) Observe(orgID, projectID string, receivedAt time.Time, metered Metered) {
	if metered.empty() {
		return
	}
	key := tenantDay{
		orgID:     orgID,
		projectID: projectID,
		day:       receivedAt.UTC().Truncate(day),
	}
	total, seen := m.totals[key]
	if !seen {
		total = &bucket{services: make(map[string]struct{})}
		m.totals[key] = total
	}
	total.counts.add(metered.Counts)
	for _, service := range metered.Services {
		total.services[service] = struct{}{}
	}
}

// Drain returns everything held and empties the meter. The caller owns the rows
// from here: if the write fails they are lost with the process, and the offsets
// that produced them were never committed, so the broker replays them.
func (m *Meter) Drain() []Row {
	if len(m.totals) == 0 {
		return nil
	}

	rows := make([]Row, 0, len(m.totals))
	for key, held := range m.totals {
		rows = append(rows, Row{
			OrgID:     key.orgID,
			ProjectID: key.projectID,
			Day:       key.day,
			Counts:    held.counts,
			Services:  sortedNames(held.services),
		})
	}
	m.totals = make(map[tenantDay]*bucket)

	// Sorted so one flush takes row locks in a stable order: two workers
	// writing the same tenants in opposite orders is a deadlock.
	sort.Slice(rows, func(i, j int) bool { return rows[i].less(rows[j]) })
	return rows
}

// Pending reports how many tenant-days are held, which is what the heartbeat
// logs and what would show a leak.
func (m *Meter) Pending() int { return len(m.totals) }

// sortedNames is a set as the writer wants it: distinct and ordered.
func sortedNames(services map[string]struct{}) []string {
	if len(services) == 0 {
		return nil
	}
	names := make([]string, 0, len(services))
	for name := range services {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// less orders rows totally: organization, then project, then day.
func (r Row) less(other Row) bool {
	switch {
	case r.OrgID != other.OrgID:
		return r.OrgID < other.OrgID
	case r.ProjectID != other.ProjectID:
		return r.ProjectID < other.ProjectID
	default:
		return r.Day.Before(other.Day)
	}
}
