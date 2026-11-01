//go:build storeprobe

package ingeststate_test

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"

	"github.com/nexovue-tech/nexovue/apps/workers/internal/pipeline/ingeststate"
)

// progressMigration is the table's own migration, so the probe writes the
// shape that ships rather than a copy of it.
const progressMigration = "../../../../../deploy/clickhouse/migrations/000037_span_derived_progress.up.sql"

// Against a real server, because KeeperMap's overwrite and the nanosecond
// received-at are the server's to get right, not a fake's. The table is created
// from its migration in a scratch database, under its own Keeper path, so the
// probe cannot touch a running worker's progress. Run with PROBE_CH_ADDR set
// (`PROBE_CH_ADDR=127.0.0.1:19000 PROBE_CH_USER=nexovue PROBE_CH_PASSWORD=nexovue_dev`).
func TestProgressRoundTripsThroughKeeperMap(t *testing.T) {
	ctx := context.Background()
	suffix := time.Now().UnixNano()
	database := fmt.Sprintf("progress_probe_%d", suffix)
	admin := dialProbe(t, "default")
	if err := admin.Do(ctx, ch.Query{Body: "CREATE DATABASE " + database}); err != nil {
		t.Fatalf("create %s: %v", database, err)
	}
	t.Cleanup(func() { _ = admin.Do(ctx, ch.Query{Body: "DROP DATABASE " + database + " SYNC"}) })

	migration, err := os.ReadFile(progressMigration)
	if err != nil {
		t.Fatalf("read the migration: %v", err)
	}
	statement := strings.Replace(string(migration), "'/span_derived_progress'",
		fmt.Sprintf("'/span_derived_progress_probe_%d'", suffix), 1)
	pool := dialProbe(t, database)
	if err := pool.Do(ctx, ch.Query{Body: statement}); err != nil {
		t.Fatalf("apply the migration: %v", err)
	}

	store := ingeststate.NewProgressStore(pool)
	partition := ingeststate.Partition{Topic: "spans-raw", ID: 3}
	first := ingeststate.Mark{CountedThrough: 41, ReceivedAt: time.Date(2026, 11, 1, 9, 0, 0, 123456789, time.UTC)}
	later := ingeststate.Mark{CountedThrough: 97, ReceivedAt: first.ReceivedAt.Add(time.Minute)}
	if err := store.Save(ctx, ingeststate.Marks{partition: first}); err != nil {
		t.Fatalf("first save: %v", err)
	}
	// Overwriting is the normal case: every settlement after the first.
	if err := store.Save(ctx, ingeststate.Marks{partition: later}); err != nil {
		t.Fatalf("second save: %v", err)
	}

	loaded, err := store.Load(ctx)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if loaded[partition] != later.CountedThrough || len(loaded) != 1 {
		t.Errorf("loaded %v, want only %s at %d", loaded, partition.Key(), later.CountedThrough)
	}

	var received proto.ColInt64
	if err := pool.Do(ctx, ch.Query{
		Body:   "SELECT toUnixTimestamp64Nano(counted_through_received_at) AS at FROM span_derived_progress",
		Result: proto.Results{{Name: "at", Data: &received}},
	}); err != nil {
		t.Fatalf("read back received_at: %v", err)
	}
	if received.Rows() != 1 || received.Row(0) != later.ReceivedAt.UnixNano() {
		t.Errorf("counted_through_received_at = %v, want %d to the nanosecond", received, later.ReceivedAt.UnixNano())
	}

	// A worker that restarts and replays an older settlement reads what is
	// stored and leaves it.
	restarted := ingeststate.NewProgress(store, ingeststate.MaxProgressHold)
	if err := restarted.Refresh(ctx); err != nil {
		t.Fatalf("refresh: %v", err)
	}
	if _, err := restarted.Record(ctx, later.ReceivedAt, ingeststate.Marks{partition: first}, nil); err != nil {
		t.Fatalf("record the replay: %v", err)
	}
	if again, _ := store.Load(ctx); again[partition] != later.CountedThrough {
		t.Errorf("a replay moved progress to %d, want it left at %d", again[partition], later.CountedThrough)
	}
}

func dialProbe(t *testing.T, database string) *chpool.Pool {
	t.Helper()
	pool, err := chpool.Dial(context.Background(), chpool.Options{ClientOptions: ch.Options{
		Address:  os.Getenv("PROBE_CH_ADDR"),
		User:     os.Getenv("PROBE_CH_USER"),
		Password: os.Getenv("PROBE_CH_PASSWORD"),
		Database: database,
	}})
	if err != nil {
		t.Fatalf("dial %s: %v", database, err)
	}
	t.Cleanup(pool.Close)
	return pool
}
