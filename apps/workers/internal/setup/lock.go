package setup

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5"
)

// clickHouseMigrationLock is the Postgres advisory lock held while ClickHouse migrates. ClickHouse
// has no lock of its own (golang-migrate's was in-process only), so two installs starting at once
// would both run the same migration; Postgres is the store every deployment already shares.
const clickHouseMigrationLock int64 = 0x6e65786f76756501 // "nexovue" and 1

// HoldMigrationLock waits for the lock and returns its release. The lock lives on its own
// connection, so it ends with the process even if release is never called.
func HoldMigrationLock(ctx context.Context, postgresDSN string) (func(), error) {
	conn, err := pgx.Connect(ctx, postgresDSN)
	if err != nil {
		return nil, fmt.Errorf("connect to Postgres for the migration lock: %w", err)
	}
	if _, err := conn.Exec(ctx, "SELECT pg_advisory_lock($1)", clickHouseMigrationLock); err != nil {
		_ = conn.Close(ctx)
		return nil, fmt.Errorf("take the migration lock: %w", err)
	}
	// Released even when ctx has already ended: a lock left behind would hold the next install.
	releaseCtx := context.WithoutCancel(ctx)
	return func() {
		_, _ = conn.Exec(releaseCtx, "SELECT pg_advisory_unlock($1)", clickHouseMigrationLock)
		_ = conn.Close(releaseCtx)
	}, nil
}
