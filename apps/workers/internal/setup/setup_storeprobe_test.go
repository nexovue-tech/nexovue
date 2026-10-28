//go:build storeprobe

package setup

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
)

// Against a real server: the repository's own migrations, applied to a scratch database from
// empty, run a second time, partly reverted and re-applied. Run with PROBE_CH_ADDR set.
func TestTheRepositorysMigrationsRunFromEmptyAndAgain(t *testing.T) {
	ctx := context.Background()
	database := fmt.Sprintf("setup_probe_%d", time.Now().UnixNano())
	admin := dial(t, "default")
	if err := admin.Do(ctx, ch.Query{Body: "CREATE DATABASE " + database}); err != nil {
		t.Fatalf("create %s: %v", database, err)
	}
	t.Cleanup(func() { _ = admin.Do(ctx, ch.Query{Body: "DROP DATABASE " + database}) })

	migrations, err := Load(osDir(t, "../../../../deploy/clickhouse/migrations"))
	if err != nil {
		t.Fatal(err)
	}
	ledger := NewClickHouseLedger(dial(t, database), LedgerTable)

	if applied, err := Up(ctx, ledger, migrations); err != nil || applied != len(migrations) {
		t.Fatalf("from empty: applied %d of %d: %v", applied, len(migrations), err)
	}
	if applied, err := Up(ctx, ledger, migrations); err != nil || applied != 0 {
		t.Fatalf("second run applied %d: %v", applied, err)
	}
	if reverted, err := Down(ctx, ledger, migrations, 2); err != nil || reverted != 2 {
		t.Fatalf("reverted %d: %v", reverted, err)
	}
	if applied, err := Up(ctx, ledger, migrations); err != nil || applied != 2 {
		t.Fatalf("re-applied %d: %v", applied, err)
	}
	version, dirty, err := ledger.Current(ctx)
	if err != nil || dirty || version != migrations[len(migrations)-1].Version {
		t.Fatalf("ledger at %d dirty=%v: %v", version, dirty, err)
	}
}

func dial(t *testing.T, database string) *chpool.Pool {
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
