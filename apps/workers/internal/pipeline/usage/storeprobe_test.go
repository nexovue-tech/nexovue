//go:build storeprobe

package usage

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// The counters statement, proved at the column it writes.
//
// `statement_test.go` reads the SQL as text and catches a counter that is
// inserted and never accumulated. This runs the statement twice against a real
// `usage_daily` and reads the row back, which is the only thing that proves the
// column exists, the check constraint admits the values, and a second flush
// adds rather than replaces (ADR 0097).
//
// Behind a build tag: it needs a running Postgres with the migrations applied,
// which `go test ./...` does not have. Run it with
//
//	PROBE_PG_DSN=postgres://nexovue:nexovue_dev@127.0.0.1:15432/nexovue \
//		go test -tags storeprobe -count=1 ./internal/pipeline/usage/
const probeSlug = "__adr_0097_probe__"

func TestBrowserBytesAccumulateInTheColumn(t *testing.T) {
	ctx := context.Background()
	dsn := os.Getenv("PROBE_PG_DSN")
	if dsn == "" {
		t.Fatal("PROBE_PG_DSN is not set; this probe needs a live control plane")
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatalf("connecting: %v", err)
	}
	t.Cleanup(pool.Close)

	orgID, projectID := seedProbeTenant(ctx, t, pool)
	store := NewPostgresStore(pool, "spans-"+probeSlug)
	day := time.Date(2026, time.September, 4, 12, 0, 0, 0, time.UTC)

	// Two flushes on the same tenant-day: the first inserts, the second has to
	// add to what the first left. A statement that only inserts passes the
	// first and loses the second.
	flushes := []struct {
		offset       int64
		bytes        int64
		browserBytes int64
	}{
		{offset: 1, bytes: 300, browserBytes: 100},
		{offset: 2, bytes: 50, browserBytes: 40},
	}
	for _, flush := range flushes {
		rows := []Row{{
			OrgID:     orgID,
			ProjectID: projectID,
			Day:       day,
			Counts:    Counts{Spans: 1, Bytes: flush.bytes, BrowserBytes: flush.browserBytes},
		}}
		progress := map[int32]Progress{0: {Offset: flush.offset, ReceivedThrough: day}}
		if _, err := store.Apply(ctx, rows, progress); err != nil {
			t.Fatalf("applying flush at offset %d: %v", flush.offset, err)
		}
	}

	var bytes, browserBytes int64
	err = pool.QueryRow(ctx,
		`SELECT bytes, browser_bytes FROM usage_daily
		 WHERE org_id = $1::uuid AND project_id = $2::uuid AND day = $3`,
		orgID, projectID, day).Scan(&bytes, &browserBytes)
	if err != nil {
		t.Fatalf("reading the row back: %v", err)
	}

	if bytes != 350 {
		t.Errorf("bytes = %d, want 350: the whole count, browser bytes included", bytes)
	}
	if browserBytes != 140 {
		t.Errorf("browser_bytes = %d, want 140 (100 + 40). A column written on the first "+
			"flush and never added to leaves the browser door judging every byte as the "+
			"server's", browserBytes)
	}
}

// seedProbeTenant makes an organization and a project for the probe to write
// against, and removes them again. The cascade takes the usage rows with them.
func seedProbeTenant(ctx context.Context, t *testing.T, pool *pgxpool.Pool) (string, string) {
	t.Helper()

	var orgID, projectID string
	err := pool.QueryRow(ctx,
		`INSERT INTO organizations (slug, name) VALUES ($1, $1) RETURNING id::text`,
		probeSlug).Scan(&orgID)
	if err != nil {
		t.Fatalf("seeding the probe organization: %v", err)
	}
	t.Cleanup(func() {
		if _, err := pool.Exec(ctx, `DELETE FROM organizations WHERE slug = $1`, probeSlug); err != nil {
			t.Errorf("removing the probe organization: %v", err)
		}
	})
	t.Cleanup(func() {
		if _, err := pool.Exec(ctx,
			`DELETE FROM `+offsetsTable+` WHERE topic = $1`, "spans-"+probeSlug); err != nil {
			t.Errorf("removing the probe offsets: %v", err)
		}
	})

	err = pool.QueryRow(ctx,
		`INSERT INTO projects (org_id, slug, name) VALUES ($1::uuid, $2, $2) RETURNING id::text`,
		orgID, probeSlug).Scan(&projectID)
	if err != nil {
		t.Fatalf("seeding the probe project: %v", err)
	}
	return orgID, projectID
}
