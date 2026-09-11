package usage

import (
	"slices"
	"testing"
	"time"
)

// onePartition is where the messages in these tests arrived. The meter keeps a
// share per partition so a handover can drop one; nothing below is
// about that, so they all use the same one and the shares never split.
const onePartition int32 = 0

func TestOneTenantDayIsOneRow(t *testing.T) {
	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt, Metered{Counts: Counts{Spans: 3, Bytes: 100}})
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt.Add(time.Hour), Metered{Counts: Counts{Spans: 4, Bytes: 200}})

	rows := meter.Drain()
	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1: two messages on one day are one row", len(rows))
	}
	if rows[0].Spans != 7 || rows[0].Bytes != 300 {
		t.Errorf("counters = %+v, want 7 spans and 300 bytes", rows[0].Counts)
	}
}

// Browser bytes accumulate beside the whole count, never instead of it: a day
// that took a page's export and a server's carries both in `bytes` and only the
// page's in `browser_bytes`.
func TestBrowserBytesAccumulateBesideTheWholeCount(t *testing.T) {
	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt,
		Metered{Counts: Counts{Spans: 1, Bytes: 100, BrowserBytes: 100}})
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt,
		Metered{Counts: Counts{Spans: 1, Bytes: 200}})
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt,
		Metered{Counts: Counts{Spans: 1, Bytes: 40, BrowserBytes: 40}})

	rows := meter.Drain()
	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(rows))
	}
	if rows[0].Bytes != 340 || rows[0].BrowserBytes != 140 {
		t.Errorf("counters = %+v, want 340 bytes of which 140 the browser's", rows[0].Counts)
	}
}

// The day boundary is UTC, and it is our own clock: two messages minutes apart
// either side of midnight belong to different days.
func TestTheDayIsAUTCDay(t *testing.T) {
	lateNight := time.Date(2026, 8, 30, 23, 59, 0, 0, time.UTC)
	justAfter := lateNight.Add(2 * time.Minute)

	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, lateNight, Metered{Counts: Counts{Spans: 1}})
	meter.Observe(onePartition, testOrg, testProject, justAfter, Metered{Counts: Counts{Spans: 1}})

	rows := meter.Drain()
	if len(rows) != 2 {
		t.Fatalf("rows = %d, want 2: the two sides of midnight are two days", len(rows))
	}
	if !rows[0].Day.Equal(time.Date(2026, 8, 30, 0, 0, 0, 0, time.UTC)) {
		t.Errorf("first day = %s, want 2026-08-30 UTC", rows[0].Day)
	}
	if !rows[1].Day.Equal(time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC)) {
		t.Errorf("second day = %s, want 2026-08-31 UTC", rows[1].Day)
	}
}

// A receive time read in another zone still lands on the UTC day, or two
// workers in different regions would meter the same message differently.
func TestTheDayDoesNotFollowTheWorkersTimezone(t *testing.T) {
	istanbul := time.FixedZone("UTC+3", 3*60*60)
	// 01:30 on the 31st in UTC+3 is 22:30 on the 30th in UTC.
	local := time.Date(2026, 8, 31, 1, 30, 0, 0, istanbul)

	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, local, Metered{Counts: Counts{Spans: 1}})

	rows := meter.Drain()
	if want := time.Date(2026, 8, 30, 0, 0, 0, 0, time.UTC); !rows[0].Day.Equal(want) {
		t.Errorf("day = %s, want %s", rows[0].Day, want)
	}
}

func TestTenantsAreMeteredApart(t *testing.T) {
	meter := NewMeter()
	meter.Observe(onePartition, "org-a", "project-a", testReceivedAt, Metered{Counts: Counts{Spans: 1}})
	meter.Observe(onePartition, "org-b", "project-b", testReceivedAt, Metered{Counts: Counts{Spans: 2}})
	meter.Observe(onePartition, "org-a", "project-other", testReceivedAt, Metered{Counts: Counts{Spans: 4}})

	rows := meter.Drain()
	if len(rows) != 3 {
		t.Fatalf("rows = %d, want 3: two organizations and two projects", len(rows))
	}
	for _, row := range rows {
		if row.OrgID == "org-a" && row.ProjectID == "project-a" && row.Spans != 1 {
			t.Errorf("org-a/project-a spans = %d, want 1", row.Spans)
		}
	}
}

// An empty export — SDKs do send them — must not open a bucket that flushes a
// row of zeros against a tenant.
func TestAnEmptyMessageIsNotMetered(t *testing.T) {
	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt, Metered{})

	if meter.Pending() != 0 {
		t.Errorf("pending = %d, want 0", meter.Pending())
	}
	if rows := meter.Drain(); rows != nil {
		t.Errorf("drained %d rows, want none", len(rows))
	}
}

func TestDrainEmptiesTheMeter(t *testing.T) {
	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt, Metered{Counts: Counts{Spans: 1}})

	if rows := meter.Drain(); len(rows) != 1 {
		t.Fatalf("first drain returned %d rows, want 1", len(rows))
	}
	if rows := meter.Drain(); len(rows) != 0 {
		t.Errorf("second drain returned %d rows, want none: a drained bucket "+
			"would be written twice", len(rows))
	}
}

// Rows are written in a stable order so two workers writing the same tenants
// take the row locks in the same order and cannot deadlock.
func TestDrainOrdersRowsForAStableWrite(t *testing.T) {
	meter := NewMeter()
	for _, org := range []string{"org-c", "org-a", "org-b"} {
		meter.Observe(onePartition, org, testProject, testReceivedAt, Metered{Counts: Counts{Spans: 1}})
	}

	rows := meter.Drain()
	for i := 1; i < len(rows); i++ {
		if rows[i-1].OrgID > rows[i].OrgID {
			t.Fatalf("rows are unordered: %s came before %s", rows[i-1].OrgID, rows[i].OrgID)
		}
	}
}

// The billing unit accumulates as a set: the same service seen in fifty
// messages is one service on the day, which is exactly what a counter could not
// do.
func TestServicesAccumulateAsASetPerTenantDay(t *testing.T) {
	meter := NewMeter()
	for range 50 {
		meter.Observe(onePartition, testOrg, testProject, testReceivedAt, Metered{
			Counts:   Counts{Spans: 1},
			Services: []string{"checkout", "payments"},
		})
	}
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt, Metered{
		Counts:   Counts{Spans: 1},
		Services: []string{"ledger"},
	})

	rows := meter.Drain()
	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(rows))
	}
	if got := rows[0].Services; !equalNames(got, []string{"checkout", "ledger", "payments"}) {
		t.Errorf("services = %v, want the three distinct names", got)
	}
	if rows[0].Spans != 51 {
		t.Errorf("spans = %d, want 51: the counters still add while the set does not", rows[0].Spans)
	}
}

// A service is billable on the day it sent something, so the two sides of
// midnight hold their own sets.
func TestAServiceIsCountedOnEachDayItSent(t *testing.T) {
	lateNight := time.Date(2026, 8, 30, 23, 59, 0, 0, time.UTC)

	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, lateNight, Metered{
		Counts: Counts{Spans: 1}, Services: []string{"checkout"},
	})
	meter.Observe(onePartition, testOrg, testProject, lateNight.Add(2*time.Minute), Metered{
		Counts: Counts{Spans: 1}, Services: []string{"checkout", "payments"},
	})

	rows := meter.Drain()
	if len(rows) != 2 {
		t.Fatalf("rows = %d, want 2", len(rows))
	}
	if !equalNames(rows[0].Services, []string{"checkout"}) {
		t.Errorf("first day services = %v, want checkout alone", rows[0].Services)
	}
	if !equalNames(rows[1].Services, []string{"checkout", "payments"}) {
		t.Errorf("second day services = %v, want both", rows[1].Services)
	}
}

// Two tenants sending a service of the same name are two billable services, one
// each. Nothing about the set may cross a tenant.
func TestTheSameServiceNameInTwoTenantsIsTwoServices(t *testing.T) {
	meter := NewMeter()
	meter.Observe(onePartition, "org-a", "project-a", testReceivedAt, Metered{
		Counts: Counts{Spans: 1}, Services: []string{"checkout"},
	})
	meter.Observe(onePartition, "org-b", "project-b", testReceivedAt, Metered{
		Counts: Counts{Spans: 1}, Services: []string{"checkout"},
	})

	rows := meter.Drain()
	if len(rows) != 2 {
		t.Fatalf("rows = %d, want 2", len(rows))
	}
	for _, row := range rows {
		if !equalNames(row.Services, []string{"checkout"}) {
			t.Errorf("%s services = %v, want checkout", row.OrgID, row.Services)
		}
	}
}

// The names are written as an array in one statement; an unstable order is a
// deadlock between two workers writing the same tenant.
func TestDrainOrdersServiceNames(t *testing.T) {
	meter := NewMeter()
	meter.Observe(onePartition, testOrg, testProject, testReceivedAt, Metered{
		Counts:   Counts{Spans: 1},
		Services: []string{"payments", "checkout", "ledger"},
	})

	rows := meter.Drain()
	if got := rows[0].Services; !slices.IsSorted(got) {
		t.Errorf("services = %v, want them sorted", got)
	}
}
