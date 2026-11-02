package setup

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// clickHouseMigrationLock is the Postgres advisory lock held while ClickHouse migrates. ClickHouse
// has no lock of its own (golang-migrate's was in-process only), so two installs starting at once
// would both run the same migration; Postgres is the store every deployment already shares.
const clickHouseMigrationLock int64 = 0x6e65786f76756501 // "nexovue" and 1

// How often a waiting setup tries the lock again, and how long it waits in all: the Postgres
// migrations' own wait (apps/api/migrations/env.py).
const (
	lockRetry = 500 * time.Millisecond
	lockWait  = 15 * time.Minute
)

// HoldMigrationLock waits for the lock and returns its release. The lock lives on its own
// connection, so it ends with the process even if release is never called. It polls rather than
// blocking in pg_advisory_lock: a blocked statement holds a snapshot, which a concurrent
// CREATE INDEX CONCURRENTLY in the Postgres migrations would have to wait out.
func HoldMigrationLock(ctx context.Context, postgresDSN string) (func(), error) {
	conn, err := pgx.Connect(ctx, postgresDSN)
	if err != nil {
		return nil, fmt.Errorf("connect to Postgres for the migration lock: %w", err)
	}
	if err := waitForLock(ctx, conn); err != nil {
		_ = conn.Close(ctx)
		return nil, err
	}
	// Released even when ctx has already ended: a lock left behind would hold the next install.
	releaseCtx := context.WithoutCancel(ctx)
	return func() {
		_, _ = conn.Exec(releaseCtx, "SELECT pg_advisory_unlock($1)", clickHouseMigrationLock)
		_ = conn.Close(releaseCtx)
	}, nil
}

func waitForLock(ctx context.Context, conn *pgx.Conn) error {
	deadline := time.Now().Add(lockWait)
	for {
		var taken bool
		err := conn.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", clickHouseMigrationLock).Scan(&taken)
		if err != nil {
			return fmt.Errorf("take the migration lock: %w", err)
		}
		if taken {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("another setup has held the ClickHouse migration lock for %s; "+
				"if it is still migrating, wait for it and run setup again", lockWait)
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("wait for the migration lock: %w", ctx.Err())
		case <-time.After(lockRetry):
		}
	}
}
