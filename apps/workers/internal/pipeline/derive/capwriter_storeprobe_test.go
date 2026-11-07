//go:build storeprobe

package derive

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/proto"
)

// overCapMigration is the table's own migration, so the probe writes the shape
// that ships.
const overCapMigration = "../../../../../deploy/clickhouse/migrations/000039_services_over_cap_1m.up.sql"

// Against a real server, because the deduplication window is the server's to
// honour. One drain over the cap is written twice in a scratch database; the
// record must read back once, by tenant and minute. Run with PROBE_CH_ADDR set,
// as TestVersionRowsSumToTheServiceRowsInTheStore is.
func TestTheOverCapRecordReadsBackOnceByTenantAndMinute(t *testing.T) {
	ctx := context.Background()
	database := fmt.Sprintf("overcap_probe_%d", time.Now().UnixNano())
	admin := dialVersionProbe(t, "default")
	if err := admin.Do(ctx, ch.Query{Body: "CREATE DATABASE " + database}); err != nil {
		t.Fatalf("create %s: %v", database, err)
	}
	t.Cleanup(func() { _ = admin.Do(ctx, ch.Query{Body: "DROP DATABASE " + database + " SYNC"}) })
	pool := dialVersionProbe(t, database)
	statement, err := os.ReadFile(overCapMigration)
	if err != nil {
		t.Fatalf("read %s: %v", overCapMigration, err)
	}
	if err := pool.Do(ctx, ch.Query{Body: string(statement)}); err != nil {
		t.Fatalf("apply %s: %v", overCapMigration, err)
	}

	// A recent minute: the TTL drops an expired row as it is inserted.
	minute := time.Now().UTC().Truncate(time.Minute)
	counters := NewCounters(0)
	for index := range MaxServicesPerTenant + 4 {
		span := inboundSpan(int32(index%3), "tenant-probe", fmt.Sprintf("svc-%05d", index), 5, false)
		span.Timestamp, span.ReceivedAt = minute.Add(10*time.Second), minute.Add(10*time.Second)
		counters.Observe(minute, span)
	}
	entries := counters.DrainAll().ServicesOverCap
	writer := NewOverCapWriter(pool)
	for attempt := range 2 {
		if err := writer.Insert(ctx, entries); err != nil {
			t.Fatalf("insert over-cap rows, attempt %d: %v", attempt+1, err)
		}
	}

	var (
		tenant proto.ColStr
		at     proto.ColDateTime
		left   proto.ColUInt64
	)
	err = pool.Do(ctx, ch.Query{
		Body: "SELECT CAST(tenant_id AS String) AS tenant, minute, sum(services_left_out) AS left_out " +
			"FROM services_over_cap_1m GROUP BY tenant_id, minute",
		Result: proto.Results{
			{Name: "tenant", Data: &tenant},
			{Name: "minute", Data: &at},
			{Name: "left_out", Data: &left},
		},
	})
	if err != nil {
		t.Fatalf("read services_over_cap_1m: %v", err)
	}
	if tenant.Rows() != 1 {
		t.Fatalf("read %d rows back, want one tenant-minute", tenant.Rows())
	}
	if tenant.Row(0) != "tenant-probe" || !at.Row(0).Equal(minute) || left.Row(0) != 4 {
		t.Fatalf("read %d rows (first: %v, %v, %v), want tenant-probe at %v with 4 left out once: "+
			"the retried insert must not be summed in twice", tenant.Rows(), tenant.Row(0), at.Row(0), left.Row(0), minute)
	}
}
