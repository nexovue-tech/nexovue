//go:build storeprobe

package derive

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// The tables' own migrations, so the probe writes the shapes that ship.
const (
	serviceMigration = "../../../../../deploy/clickhouse/migrations/000035_service_stats_1m.up.sql"
	versionMigration = "../../../../../deploy/clickhouse/migrations/000038_service_versions_1m.up.sql"
)

// Against a real server, because the `sumForEach` state, the deduplication
// window and the `input()` insert are the server's to get right. One drain is
// written to both rollups in a scratch database; the version rows must sum to
// the service rows bucket by bucket (AC15), and inserting the same drain again
// must change nothing (AC16). Run with PROBE_CH_ADDR set
// (`PROBE_CH_ADDR=127.0.0.1:19000 PROBE_CH_USER=nexovue PROBE_CH_PASSWORD=nexovue_dev`).
func TestVersionRowsSumToTheServiceRowsInTheStore(t *testing.T) {
	ctx := context.Background()
	database := fmt.Sprintf("versions_probe_%d", time.Now().UnixNano())
	admin := dialVersionProbe(t, "default")
	if err := admin.Do(ctx, ch.Query{Body: "CREATE DATABASE " + database}); err != nil {
		t.Fatalf("create %s: %v", database, err)
	}
	t.Cleanup(func() { _ = admin.Do(ctx, ch.Query{Body: "DROP DATABASE " + database + " SYNC"}) })
	pool := dialVersionProbe(t, database)
	for _, migration := range []string{serviceMigration, versionMigration} {
		statement, err := os.ReadFile(migration)
		if err != nil {
			t.Fatalf("read %s: %v", migration, err)
		}
		if err := pool.Do(ctx, ch.Query{Body: string(statement)}); err != nil {
			t.Fatalf("apply %s: %v", migration, err)
		}
	}

	// A recent minute: the tables' TTL drops an expired row as it is inserted.
	minute := time.Now().UTC().Truncate(time.Minute)
	counters := NewCounters(0)
	for index := range 900 {
		built := versionedSpan(int32(index%3), fmt.Sprintf("svc-%d", index%2),
			fmt.Sprintf("v%d", index%25), float64(index%400)*7.3, index%11 == 0)
		built.Timestamp, built.ReceivedAt = minute.Add(10*time.Second), minute.Add(10*time.Second)
		counters.Observe(minute, built)
	}
	drained := counters.DrainAll()
	services, versions := NewServiceWriter(pool), NewVersionWriter(pool)
	for attempt := range 2 {
		if err := services.Insert(ctx, drained.Services); err != nil {
			t.Fatalf("insert service rows, attempt %d: %v", attempt+1, err)
		}
		if err := versions.Insert(ctx, drained.Versions); err != nil {
			t.Fatalf("insert version rows, attempt %d: %v", attempt+1, err)
		}
	}

	serviceTotals := readTotals(t, pool, "service_stats_1m")
	versionTotals := readTotals(t, pool, "service_versions_1m")
	if len(serviceTotals) != 2 {
		t.Fatalf("read %d services back, want 2", len(serviceTotals))
	}
	for service, want := range serviceTotals {
		if got := versionTotals[service]; got != want {
			t.Errorf("%s: versions sum to %+v in the store, the service row holds %+v", service, got, want)
		}
	}
	var drainedRequests uint64
	for _, stat := range drained.Services {
		drainedRequests += stat.RequestCount
	}
	var stored uint64
	for _, totals := range serviceTotals {
		stored += totals.requests
	}
	if stored != drainedRequests {
		t.Errorf("the store holds %d requests, the drain %d: the retried insert was summed in twice",
			stored, drainedRequests)
	}
}

// storedTotals is one service's counts as a reader sees them.
type storedTotals struct {
	requests, errors uint64
	buckets          string
}

func readTotals(t *testing.T, pool *chpool.Pool, table string) map[string]storedTotals {
	t.Helper()
	var (
		service  proto.ColStr
		requests proto.ColUInt64
		errors   proto.ColUInt64
		buckets  proto.ColStr
	)
	err := pool.Do(context.Background(), ch.Query{
		Body: "SELECT CAST(service_name AS String) AS service, sum(request_count) AS requests, sum(error_count) AS errors, " +
			"toString(sumForEachMerge(latency_buckets)) AS buckets FROM " + table + " GROUP BY service_name",
		Result: proto.Results{
			{Name: "service", Data: &service},
			{Name: "requests", Data: &requests},
			{Name: "errors", Data: &errors},
			{Name: "buckets", Data: &buckets},
		},
	})
	if err != nil {
		t.Fatalf("read %s: %v", table, err)
	}
	found := make(map[string]storedTotals, service.Rows())
	for row := range service.Rows() {
		found[service.Row(row)] = storedTotals{requests.Row(row), errors.Row(row), buckets.Row(row)}
	}
	return found
}

func dialVersionProbe(t *testing.T, database string) *chpool.Pool {
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
