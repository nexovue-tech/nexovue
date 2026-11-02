//go:build storeprobe

package setup

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// Against a real Postgres: while another session holds the lock, the waiting setup must sit in
// no statement. A session blocked inside pg_advisory_lock holds a snapshot, which a concurrent
// CREATE INDEX CONCURRENTLY in the Postgres migrations would have to wait out. Run with
// PROBE_PG_DSN=postgres://nexovue:nexovue_dev@127.0.0.1:15432/nexovue.
func TestAWaitingSetupHoldsNoRunningStatement(t *testing.T) {
	ctx := context.Background()
	dsn := os.Getenv("PROBE_PG_DSN")
	if dsn == "" {
		t.Fatal("PROBE_PG_DSN is not set; this probe needs a live Postgres")
	}
	holder, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = holder.Close(ctx) }()
	if _, err := holder.Exec(ctx, "SELECT pg_advisory_lock($1)", clickHouseMigrationLock); err != nil {
		t.Fatal(err)
	}

	type result struct {
		release func()
		err     error
	}
	done := make(chan result, 1)
	go func() {
		release, err := HoldMigrationLock(ctx, dsn)
		done <- result{release, err}
	}()

	observer, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = observer.Close(ctx) }()
	blocked := 0
	for range 20 {
		var n int
		err := observer.QueryRow(ctx, `SELECT count(*) FROM pg_stat_activity
			WHERE wait_event_type = 'Lock' AND wait_event = 'advisory'`).Scan(&n)
		if err != nil {
			t.Fatal(err)
		}
		blocked += n
		time.Sleep(50 * time.Millisecond)
	}
	if blocked > 0 {
		t.Errorf("the waiting setup sat blocked in a lock statement in %d samples", blocked)
	}

	if _, err := holder.Exec(ctx, "SELECT pg_advisory_unlock($1)", clickHouseMigrationLock); err != nil {
		t.Fatal(err)
	}
	select {
	case got := <-done:
		if got.err != nil {
			t.Fatalf("the lock was released, but the setup did not take it: %v", got.err)
		}
		got.release()
	case <-time.After(10 * time.Second):
		t.Fatal("the setup did not take the lock within 10 s of its release")
	}
}
