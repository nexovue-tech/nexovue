package usage

import (
	"testing"
	"time"
)

func TestOneTenantDayIsOneRow(t *testing.T) {
	meter := NewMeter()
	meter.Observe(testOrg, testProject, testReceivedAt, Counts{Spans: 3, Bytes: 100})
	meter.Observe(testOrg, testProject, testReceivedAt.Add(time.Hour), Counts{Spans: 4, Bytes: 200})

	rows := meter.Drain()
	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1: two messages on one day are one row", len(rows))
	}
	if rows[0].Spans != 7 || rows[0].Bytes != 300 {
		t.Errorf("counters = %+v, want 7 spans and 300 bytes", rows[0].Counts)
	}
}

// The day boundary is UTC, and it is our own clock: two messages minutes apart
// either side of midnight belong to different days.
func TestTheDayIsAUTCDay(t *testing.T) {
	lateNight := time.Date(2026, 8, 30, 23, 59, 0, 0, time.UTC)
	justAfter := lateNight.Add(2 * time.Minute)

	meter := NewMeter()
	meter.Observe(testOrg, testProject, lateNight, Counts{Spans: 1})
	meter.Observe(testOrg, testProject, justAfter, Counts{Spans: 1})

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
	meter.Observe(testOrg, testProject, local, Counts{Spans: 1})

	rows := meter.Drain()
	if want := time.Date(2026, 8, 30, 0, 0, 0, 0, time.UTC); !rows[0].Day.Equal(want) {
		t.Errorf("day = %s, want %s", rows[0].Day, want)
	}
}

func TestTenantsAreMeteredApart(t *testing.T) {
	meter := NewMeter()
	meter.Observe("org-a", "project-a", testReceivedAt, Counts{Spans: 1})
	meter.Observe("org-b", "project-b", testReceivedAt, Counts{Spans: 2})
	meter.Observe("org-a", "project-other", testReceivedAt, Counts{Spans: 4})

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
	meter.Observe(testOrg, testProject, testReceivedAt, Counts{})

	if meter.Pending() != 0 {
		t.Errorf("pending = %d, want 0", meter.Pending())
	}
	if rows := meter.Drain(); rows != nil {
		t.Errorf("drained %d rows, want none", len(rows))
	}
}

func TestDrainEmptiesTheMeter(t *testing.T) {
	meter := NewMeter()
	meter.Observe(testOrg, testProject, testReceivedAt, Counts{Spans: 1})

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
		meter.Observe(org, testProject, testReceivedAt, Counts{Spans: 1})
	}

	rows := meter.Drain()
	for i := 1; i < len(rows); i++ {
		if rows[i-1].OrgID > rows[i].OrgID {
			t.Fatalf("rows are unordered: %s came before %s", rows[i-1].OrgID, rows[i].OrgID)
		}
	}
}
