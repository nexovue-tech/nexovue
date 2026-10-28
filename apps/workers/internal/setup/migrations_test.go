package setup

import (
	"context"
	"errors"
	"slices"
	"testing"
	"testing/fstest"
)

// memoryLedger is the ledger as a list, so each test can read exactly what was recorded.
type memoryLedger struct {
	rows       []ledgerRow
	statements []string
	failOn     string
}

type ledgerRow struct {
	version int64
	dirty   bool
}

func (l *memoryLedger) Ensure(context.Context) error { return nil }

func (l *memoryLedger) Current(context.Context) (int64, bool, error) {
	if len(l.rows) == 0 {
		return NoVersion, false, nil
	}
	last := l.rows[len(l.rows)-1]
	return last.version, last.dirty, nil
}

func (l *memoryLedger) Record(_ context.Context, version int64, dirty bool) error {
	l.rows = append(l.rows, ledgerRow{version, dirty})
	return nil
}

func (l *memoryLedger) Exec(_ context.Context, statement string) error {
	if statement == l.failOn {
		return errors.New("the server refused it")
	}
	l.statements = append(l.statements, statement)
	return nil
}

func files() fstest.MapFS {
	return fstest.MapFS{
		"000001_spans.up.sql":     {Data: []byte("CREATE TABLE spans\n")},
		"000001_spans.down.sql":   {Data: []byte("DROP TABLE spans")},
		"000002_logs.up.sql":      {Data: []byte("CREATE TABLE logs")},
		"000002_logs.down.sql":    {Data: []byte("DROP TABLE logs")},
		"000010_metrics.up.sql":   {Data: []byte("CREATE TABLE metrics")},
		"000010_metrics.down.sql": {Data: []byte("DROP TABLE metrics")},
		"README.md":               {Data: []byte("not a migration")},
	}
}

func loaded(t *testing.T) []Migration {
	t.Helper()
	migrations, err := Load(files())
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	return migrations
}

func TestMigrationsLoadInOrderWithBothHalves(t *testing.T) {
	migrations := loaded(t)

	var versions []int64
	for _, m := range migrations {
		versions = append(versions, m.Version)
	}
	if !slices.Equal(versions, []int64{1, 2, 10}) {
		t.Fatalf("versions = %v", versions)
	}
	if migrations[0].Up != "CREATE TABLE spans" || migrations[0].Name != "spans" {
		t.Fatalf("first = %+v", migrations[0])
	}
}

func TestAMigrationWithoutItsDownFileIsRefused(t *testing.T) {
	broken := files()
	delete(broken, "000002_logs.down.sql")

	if _, err := Load(broken); err == nil {
		t.Fatal("a migration with no down file loaded")
	}
}

func TestUpAppliesEveryMigrationOnceAndMarksEachDirtyWhileItRuns(t *testing.T) {
	ledger := &memoryLedger{}
	migrations := loaded(t)

	applied, err := Up(context.Background(), ledger, migrations)
	if err != nil || applied != 3 {
		t.Fatalf("applied %d, err %v", applied, err)
	}
	want := []ledgerRow{{1, true}, {1, false}, {2, true}, {2, false}, {10, true}, {10, false}}
	if !slices.Equal(ledger.rows, want) {
		t.Fatalf("rows = %v", ledger.rows)
	}

	again, err := Up(context.Background(), ledger, migrations)
	if err != nil || again != 0 || len(ledger.statements) != 3 {
		t.Fatalf("a second run applied %d (%v), statements %d", again, err, len(ledger.statements))
	}
}

func TestUpStartsAfterTheVersionGolangMigrateLeft(t *testing.T) {
	ledger := &memoryLedger{rows: []ledgerRow{{2, false}}}

	applied, err := Up(context.Background(), ledger, loaded(t))
	if err != nil || applied != 1 || !slices.Equal(ledger.statements, []string{"CREATE TABLE metrics"}) {
		t.Fatalf("applied %d, err %v, ran %v", applied, err, ledger.statements)
	}
}

func TestAFailedMigrationIsLeftDirtyAndBlocksTheNextRun(t *testing.T) {
	ledger := &memoryLedger{failOn: "CREATE TABLE logs"}
	migrations := loaded(t)

	if _, err := Up(context.Background(), ledger, migrations); err == nil {
		t.Fatal("a refused statement was not reported")
	}
	if version, dirty, _ := ledger.Current(context.Background()); version != 2 || !dirty {
		t.Fatalf("ledger at %d dirty=%v", version, dirty)
	}

	ledger.failOn = ""
	if _, err := Up(context.Background(), ledger, migrations); !errors.Is(err, ErrDirty) {
		t.Fatalf("a dirty ledger did not stop the run: %v", err)
	}
}

func TestDownRevertsTheNewestAndRecordsThePreviousVersion(t *testing.T) {
	ledger := &memoryLedger{rows: []ledgerRow{{10, false}}}

	reverted, err := Down(context.Background(), ledger, loaded(t), 2)
	if err != nil || reverted != 2 {
		t.Fatalf("reverted %d, err %v", reverted, err)
	}
	if !slices.Equal(ledger.statements, []string{"DROP TABLE metrics", "DROP TABLE logs"}) {
		t.Fatalf("ran %v", ledger.statements)
	}
	if version, dirty, _ := ledger.Current(context.Background()); version != 1 || dirty {
		t.Fatalf("ledger at %d dirty=%v", version, dirty)
	}
}

func TestTheRepositorysOwnMigrationsLoad(t *testing.T) {
	migrations, err := Load(osDir(t, "../../../../deploy/clickhouse/migrations"))
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(migrations) < 33 {
		t.Fatalf("only %d migrations", len(migrations))
	}
}
