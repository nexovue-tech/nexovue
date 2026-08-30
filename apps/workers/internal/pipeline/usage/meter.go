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

// Row is one tenant-day's counters, ready to be written.
type Row struct {
	OrgID     string
	ProjectID string
	Day       time.Time
	Counts
}

// Meter accumulates counters in memory and hands them out in flushes, the way
// derive accumulates per-minute counters: a write per message would put a
// Postgres round trip in the path of every ingested batch.
//
// It is not safe for concurrent use; one pipeline goroutine owns it.
type Meter struct {
	totals map[tenantDay]*Counts
}

// NewMeter returns an empty meter.
func NewMeter() *Meter {
	return &Meter{totals: make(map[tenantDay]*Counts)}
}

// Observe adds one message's counts to the day it was received on.
//
// receivedAt is our own clock, taken from the envelope the receiver stamped. A
// message carrying nothing — an empty export, which SDKs do send — is dropped
// here rather than opening a bucket that would flush a row of zeros.
func (m *Meter) Observe(orgID, projectID string, receivedAt time.Time, counts Counts) {
	if counts.empty() {
		return
	}
	key := tenantDay{
		orgID:     orgID,
		projectID: projectID,
		day:       receivedAt.UTC().Truncate(day),
	}
	total, seen := m.totals[key]
	if !seen {
		total = &Counts{}
		m.totals[key] = total
	}
	total.add(counts)
}

// Drain returns everything held and empties the meter. The caller owns the rows
// from here: if the write fails they are lost with the process, and the offsets
// that produced them were never committed, so the broker replays them.
func (m *Meter) Drain() []Row {
	if len(m.totals) == 0 {
		return nil
	}

	rows := make([]Row, 0, len(m.totals))
	for key, counts := range m.totals {
		rows = append(rows, Row{
			OrgID:     key.orgID,
			ProjectID: key.projectID,
			Day:       key.day,
			Counts:    *counts,
		})
	}
	m.totals = make(map[tenantDay]*Counts)

	// Sorted so one flush takes row locks in a stable order: two workers
	// writing the same tenants in opposite orders is a deadlock.
	sort.Slice(rows, func(i, j int) bool { return rows[i].less(rows[j]) })
	return rows
}

// Pending reports how many tenant-days are held, which is what the heartbeat
// logs and what would show a leak.
func (m *Meter) Pending() int { return len(m.totals) }

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
