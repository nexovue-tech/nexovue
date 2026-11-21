# Status: Backup manifest counts

## Lifecycle status

Implemented

## Current task

None.

## Completed

- Found the cause from the CI timestamps, and reproduced it with containers of this change's own:
  a job queued after `pg_dump` gave `pg.jobs=2` beside a one-row dump and the same
  `FAIL jobs expected 2, got 1`.
- `backup.sh` reads the Postgres row counts, the alembic revision and the meter's watermark count out
  of `postgres.dump` itself (`pg_dump_rows` in `common.sh`).
- Regression test `apps/api/tests/test_backup_manifest_live.py`, seen failing and then passing.

## Next action

Founder reviews the diff. Once it is on the branch, push and read the `insight-fence` run: AC46's
verify should pass, and what follows it (the grant check, the AI account suites) has never run.

## Open decisions

- **ClickHouse has the same flaw, and it is not fixed here.** `ch.*` counts are still read live after
  `BACKUP DATABASE`. Under live ingest, a backup can record more rows than it holds, so a restore of
  a daily backup would fail verification and leave nexovue stopped. The manifest's header already
  says this. The fence has no ingest, so it is not affected. The candidate fix is to count the
  artifact. On ClickHouse 26.3 here, `CREATE DATABASE … ENGINE = Backup('nexovue', Disk('backups',
  '<name>.zip'))` attached every MergeTree-family table of a snapshot read-only, with no setting
  needed. It did not attach `schema_migrations` (TinyLog) or the KeeperMap tables. So
  `clickhouse_schema_version` would still be read live (it only moves on a migration). This could
  not be checked against ClickHouse's documentation (clickhouse.com is not reachable from this
  session), so it is a measurement, not a researched recommendation. The decision needed is whether
  to make that change.

## Blocked

Nothing.

## Risks

- Reading the dump back costs one decompression of `postgres.dump` per backup, about the same time
  as the dump itself. That is negligible today. Not measured at scale.
- The new test runs a whole backup of the dev stack inside `just check`. Here it takes 17 to 30
  seconds. It has not been run under CI's `-n 6` alongside the other suites.

## Validation

See `bug.md`, *Fix plan and validation*. Not run here: the `insight-fence` job itself, because its
preflight wants more disk than this machine has, and `just check` whole.

## Specification impact

None.

## Documentation impact

`docs/operations/backup-and-restore.md` now says where the manifest's counts come from. The header
`backup.sh` writes into each manifest says the same.

## Last updated

2026-11-21
