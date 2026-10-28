package setup

import (
	"context"
	"fmt"
	"time"

	"github.com/ClickHouse/ch-go"
	"github.com/ClickHouse/ch-go/chpool"
	"github.com/ClickHouse/ch-go/proto"
)

// LedgerTable is golang-migrate's table, kept so a database it migrated is read as it left it.
const LedgerTable = "schema_migrations"

// ClickHouseLedger records migrations in LedgerTable, in golang-migrate's shape: one row per
// change, the latest by `sequence` the current state.
type ClickHouseLedger struct {
	pool  *chpool.Pool
	table string
	now   func() time.Time
}

// NewClickHouseLedger keeps the ledger in table, which tests point at a scratch name.
func NewClickHouseLedger(pool *chpool.Pool, table string) *ClickHouseLedger {
	return &ClickHouseLedger{pool: pool, table: table, now: time.Now}
}

// Ensure creates the ledger table if this database has none.
func (l *ClickHouseLedger) Ensure(ctx context.Context) error {
	// TinyLog, as golang-migrate created it: a handful of rows, written one at a time.
	return l.Exec(ctx, "CREATE TABLE IF NOT EXISTS "+l.table+
		" (version Int64, dirty UInt8, sequence UInt64) ENGINE = TinyLog")
}

// Current reads the newest row: the version reached, and whether its migration stopped halfway.
func (l *ClickHouseLedger) Current(ctx context.Context) (int64, bool, error) {
	var (
		version proto.ColInt64
		dirty   proto.ColUInt8
	)
	err := l.pool.Do(ctx, ch.Query{
		Body: "SELECT version, dirty FROM " + l.table + " ORDER BY sequence DESC LIMIT 1",
		Result: proto.Results{
			{Name: "version", Data: &version},
			{Name: "dirty", Data: &dirty},
		},
	})
	if err != nil {
		return 0, false, fmt.Errorf("reading %s: %w", l.table, err)
	}
	if version.Rows() == 0 {
		return NoVersion, false, nil
	}
	return version.Row(0), dirty.Row(0) == 1, nil
}

// Record appends a row; the newest by sequence is the current state.
func (l *ClickHouseLedger) Record(ctx context.Context, version int64, dirty bool) error {
	var (
		versions  proto.ColInt64
		dirties   proto.ColUInt8
		sequences proto.ColUInt64
	)
	versions.Append(version)
	dirties.Append(boolToUInt8(dirty))
	sequences.Append(uint64(l.now().UnixNano()))
	err := l.pool.Do(ctx, ch.Query{
		Body: "INSERT INTO " + l.table + " (version, dirty, sequence) VALUES",
		Input: proto.Input{
			{Name: "version", Data: &versions},
			{Name: "dirty", Data: &dirties},
			{Name: "sequence", Data: &sequences},
		},
	})
	if err != nil {
		return fmt.Errorf("writing %s: %w", l.table, err)
	}
	return nil
}

// Exec runs one statement.
func (l *ClickHouseLedger) Exec(ctx context.Context, statement string) error {
	return l.pool.Do(ctx, ch.Query{Body: statement})
}

func boolToUInt8(value bool) uint8 {
	if value {
		return 1
	}
	return 0
}
