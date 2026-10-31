# Database engineering standard

- Postgres schema changes use Alembic; ClickHouse schema changes use the checked-in migration system.
- Tenant-scoped Postgres access takes `org_id`; ClickHouse queries go through the tenancy repository chokepoint.
- Use explicit transactions where related control-plane writes must succeed together.
- Do not create a combined cross-store migration/rollback abstraction: the stores have independent lifecycle and recovery behavior.
- Test forward migration, rollback/recovery implications, indexes, and compatibility for material schema changes.

## Postgres migrations on tables that hold data

Enforced by `apps/api/tests/test_migration_rules.py` where a test can see it (readiness group 9, C6).

- **Every migration runs with `lock_timeout = 5s`** (`migrations/env.py`). A migration that waits for a
  lock behind a long transaction would hold every later write to that table behind it; with the timeout
  it fails, and is run again when the table is quiet.
- **An index on a table that already holds rows is built `CONCURRENTLY`**, in a migration of its own that
  runs outside a transaction (`op.get_context().autocommit_block()`), because `CREATE INDEX` without it
  blocks every write for as long as the build takes. An index on a table created in the same migration
  needs neither. A failed concurrent build leaves an invalid index behind, so the migration drops one
  of the same name first (`DROP INDEX CONCURRENTLY IF EXISTS`), and its downgrade drops concurrently too.
- **A setup waiting for another's migrations holds no transaction and runs no statement.** A concurrent
  build waits for every session holding a snapshot, including one blocked in `pg_advisory_lock` even
  in autocommit, while that session waits for the builder's lock: Postgres ends one as a deadlock.
  `migrations/env.py` therefore polls `pg_try_advisory_lock` in autocommit, and gives up with an error
  after 15 minutes. `test_setup_live.py` holds both.
- **A downgrade that would drop records that exist nowhere else raises** "roll forward only" rather than
  dropping them: going back past it is a restore. `0020`, `0022` and `0026` (billing) are those today.
