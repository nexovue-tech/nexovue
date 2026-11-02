//go:build storeprobe

package intake

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// The refusal upsert, proved against the real `intake_refusals` (migration 0041):
// its key, its CHECKs, and that a retry never lowers a count.
//
// Behind a build tag, like the allowance probe: it needs a running Postgres with
// the migrations applied. Run it with
//
//	PROBE_PG_DSN=postgres://nexovue:nexovue_dev@127.0.0.1:15432/nexovue \
//		go test -tags storeprobe -count=1 ./internal/intake/

const probeReceiver = "__f6_refusals_probe__"

func TestARefusalMinuteIsUpsertedOncePerReceiverAndNeverLowered(t *testing.T) {
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
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			`DELETE FROM intake_refusals WHERE receiver_id = $1`, probeReceiver)
	})

	writer := NewPostgresRefusals(pool)
	minute := time.Now().UTC().Truncate(time.Minute).Add(-time.Minute)
	for _, count := range []int{7, 5, 9} {
		if err := writer.RecordRefusals(ctx, minute, probeReceiver, ReasonBrokerUnavailable, count); err != nil {
			t.Fatalf("recording %d: %v", count, err)
		}
	}

	var rows, stored int
	var at time.Time
	if err := pool.QueryRow(ctx, `
		SELECT count(*), max(count), max(minute) FROM intake_refusals WHERE receiver_id = $1`,
		probeReceiver,
	).Scan(&rows, &stored, &at); err != nil {
		t.Fatalf("reading back: %v", err)
	}
	if rows != 1 || stored != 9 || !at.Equal(minute) {
		t.Fatalf("rows = %d, count = %d, minute = %v; want 1 row of 9 at %v", rows, stored, at, minute)
	}

	if err := writer.RecordRefusals(ctx, minute, probeReceiver, "unavailable", 9); err == nil {
		t.Fatal("a reason the table does not admit was written")
	}
}
