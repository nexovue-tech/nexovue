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

- ClickHouse's `ch.*` counts and `clickhouse_schema_version` read from `clickhouse.zip` (`ch_backup_open`
  in `common.sh`, used by `backup.sh`), with a regression test seen failing, 2026-11-22.

## Next action

Founder reviews the diff. Once it is on the branch, push and read the `insight-fence` run: AC46's
verify should pass, and what follows it (the grant check, the AI account suites) has never run.

## Open decisions

- None. ClickHouse's counts were the open decision; the founder settled it on 2026-11-22
  (foundation item 35d): count the archive, as Postgres's are. Done, see `bug.md`, *Second fix*.

## Blocked

Nothing.

## Risks

- Reading the dump back costs one decompression of `postgres.dump` per backup, about the same time
  as the dump itself. That is negligible today. Not measured at scale.
- The second fix restores the row-merging tables (rollups, `detection_readings`) into a scratch
  database for each backup, so a backup briefly needs their size in extra ClickHouse disk. Not
  measured at scale; the runbook's "What it costs" is not updated because that section does not exist
  in `docs/operations/backup-and-restore.md` today.
- The new test runs a whole backup of the dev stack inside `just check`. Here it takes 17 to 30
  seconds. It has not been run under CI's `-n 6` alongside the other suites.

## Validation

See `bug.md`, *Fix plan and validation* and *Second fix*. Not run here: the `insight-fence` job itself, because its
preflight wants more disk than this machine has, and `just check` whole.

## Specification impact

None.

## Documentation impact

`docs/operations/backup-and-restore.md` now says where the manifest's counts come from. The header
`backup.sh` writes into each manifest says the same.

## Last updated

2026-11-22
