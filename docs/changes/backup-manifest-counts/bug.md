# Bug: A backup's manifest counted Postgres rows after the dump, not in it

## Reported behavior

CI run 44912704513, job `insight-fence`, AC46. `nexovue backup` took snapshot `2026-11-21T141719Z`
with the whole stack running; it checked out (manifest, `postgres.dump`, `clickhouse.zip`,
`bucket.tar`). Postgres and ClickHouse were restored onto an empty Postgres volume, and the
post-restore `verify.sh` printed:

```
  ok    job_results                              0
  FAIL  jobs                                     expected 2, got 1
FAIL — 1 check(s) did not match 2026-11-21T141719Z
```

Every other table matched. Because `verify.sh` failed, `nexovue restore` stopped before `start`.

## Reproduction

The CI timestamps already point at the cause:

| Time (UTC) | Step |
|---|---|
| 14:17:19.54 | `postgres: dumping` (`pg_dump`, with every service running) |
| 14:17:19.73 | `clickhouse: backing up`. The dump is finished. |
| 14:17:19.97 to 14:17:42.03 | `manifest: measuring`: ClickHouse counts, then one live `SELECT count(*)` per Postgres table |
| 14:17:43 to 14:17:55 | services stopped, containers removed, Postgres volume deleted |
| 14:18:17 to 14:18:23 | restore, `setup`, bucket import (no service running) |
| 14:18:31.85 | `FAIL jobs expected 2, got 1` |

`backup.sh` read `pg.jobs` up to about 22 seconds after `pg_dump` had finished. During that time
`scheduler-beat` and the workers were still running, and `jobs` is their work queue.

Reproduced on this machine with containers of its own (`fj-postgres` on the dev compose file's
pgvector image, `fj-clickhouse` on its ClickHouse image and config, both migrated with `setup`). A
pass-through `docker` on PATH queued one job the moment `pg_dump` returned, which is what beat does
at its own pace. Then came the store steps `restore_snapshot` runs: empty both stores, restore, verify.

Before the fix:

```
pg.jobs=2                                  (manifest)
COPY public.jobs ... FROM stdin;           (the dump: one row, 'before-dump')
  FAIL  jobs                                     expected 2, got 1
FAIL — 1 check(s) did not match 2026-11-21T165007Z
```

That is CI's failure, line for line.

## Root cause

**(c), a counting bug: the counts were taken outside the dump's snapshot.** `pg_dump` reads every
table in one snapshot. `backup.sh` then counted each table live, after the ClickHouse backup. Any
table the running stack wrote in between got a manifest count the dump did not hold. Rows the stack
deleted in between made the count lower than the dump. `jobs` is the table most likely to change in
that gap. `sessions`, `audit_log`, `rate_limit_windows` and the alembic revision had the same
exposure. The same was true of every daily backup (`nexovue-backup.timer`) and every pre-upgrade
backup, because both run with the stack up. So restoring one of those could fail verification with
nothing lost, and the restore would then leave nexovue stopped.

The other two explanations are ruled out:

- **(a) The restore lost a row.** No. `pg_restore --single-transaction` either restores the whole
  dump or fails. The reproduction restored exactly the one row the dump held. In CI the restore
  printed no error.
- **(b) Something consumed a job between the restore and verify.** No. In that window only `setup`
  ran (Alembic already at head, the AI account's grants, the bucket) and the bucket import. Every
  service had been stopped and its container removed at 14:17:55, and `start` comes after
  `verify.sh`. `setup` does not touch `jobs` (`nexovue_api/setup.py`, `insight_account.py`). In any
  case, nothing done to the live database after `pg_dump` can reach the dump.

The one CI fact this cannot settle is which task beat queued. The run kept no database to ask. It
does not affect the fix, which covers any write.

## Specification impact

None. A backup is the same artifact. Its manifest now describes that artifact exactly.

## Regression test

`apps/api/tests/test_backup_manifest_live.py`, live: it needs the dev stack's Postgres and ClickHouse.
A pass-through `docker` writes one row to a probe table the moment `pg_dump` returns. The dump is
then restored into a scratch database and counted there, so the test's oracle shares no code with
the script. The test asserts three things. First, the manifest holds the dump's count for the probe
(1, while the live table holds 2). Second, every `pg.*` line equals the restored database's count,
with the same set of tables. Third, the watermark line and `alembic_revision` match the dump. Its
ClickHouse half backs up a near-empty database of its own, so other suites creating and dropping
tables in `nexovue` under `-n 6` cannot fail it.

Seen failing on the original scripts: `assert manifest["pg.<probe>"] == "1"`, got `'2'`. It passes
with the fix.

## Fix plan and validation

The fix reads every Postgres fact in the manifest out of `postgres.dump` itself.
`pg_dump_rows` (`deploy/backup/common.sh`) streams the dump through
`pg_restore --data-only --file=-` and counts each table's COPY lines. COPY's text format writes one
line per row and escapes a newline inside a value. The function also takes `alembic_version`'s row.
`backup.sh` builds the `pg.*` lines, `alembic_revision` and `watermark.pg.usage_ingest_offsets`
from that output. If the dump cannot be read back, the backup fails, so a snapshot whose dump is
unreadable is never reported complete. A partitioned table's rows sit under its partitions, so only
the partitions are named. There are none today.

`verify.sh` is unchanged. It still compares every table's live `count(*)` after a restore with the
manifest, `jobs` included. **It still catches a real loss**: after the fixed round trip, deleting
the restored job made it print `FAIL jobs expected 1, got 0`. The rule is simply exact again. The
manifest holds what the dump holds, so any difference after a restore is a restore that did not put
the dump back. A busy queue is not the cause.

Rejected:

- **Dropping or loosening the `jobs` check.** The check was right and the number was wrong. Loosening
  it would also hide a genuinely lost queued job, for example a reminder or an erasure step.
- **Counting inside an exported snapshot** (`pg_export_snapshot()` with `pg_dump --snapshot`). It is
  equally exact. But it needs a psql session held open across `pg_dump`, which bash 3.2 (a Mac
  developer's `just backup`) can only do with a FIFO and a background process. Reading the artifact
  also proves the file is readable, and that is what a restore will actually use.

Validation (this machine, own containers):

- Round trip with a job queued after the dump: the manifest says `pg.jobs=1` while the live table
  holds 2. Restore, then verify: `PASS — the running stack matches 2026-11-21T165444Z`.
- The regression test fails before the fix and passes after it (1 passed).
- `test_backups_live.py`: 4 passed, against SeaweedFS in a container of its own.
  `test_backup_retention.py` and `test_install_script.py`: 27 passed.
- Shellcheck v0.11.0 over `lint-deploy`'s file list: clean. `ruff check`, `ruff format --check`:
  clean. `mypy .`: 6 errors, all `import-not-found` for `dns` (dnspython) in
  `probes/dns_check.py` and `test_tcp_and_dns.py`. These files are untouched and dnspython is not
  installed in this environment. None of the errors is in a changed file.

## Second fix: ClickHouse's counts (founder, 2026-11-22, foundation item 35d)

Same flaw, same cure. `ch.*` counts and `clickhouse_schema_version` were read live after `BACKUP
DATABASE`, so a backup taken under ingest could record more rows than it holds, and a restore would
fail verification with nothing lost and leave nexovue stopped. They are now read from the archive.
This is a second fix on this record, not a sibling: one cause (counting outside the artifact), one
manifest, one `verify.sh` rule, and the first fix's open decision named it.

**How** (`ch_backup_open` in `common.sh`, used by `backup.sh`). The staged `<name>.zip` is opened
with `CREATE DATABASE … ENGINE = Backup('nexovue', Disk('backups', '<name>.zip'))`, which shows the
MergeTree-family tables read-only with no data copied. Two things it cannot do, both measured on
ClickHouse 26.3.20 here (clickhouse.com was not reachable, so this is measurement, not researched
documentation):

- It does not attach `schema_migrations` (TinyLog) or the KeeperMap tables. `schema_migrations` is
  restored into a scratch database (`CLICKHOUSE_TABLES_NOT_ATTACHABLE`), and `clickhouse_schema_version`
  and `ch.schema_migrations` are read there. The KeeperMap watermarks stay live lines, which
  `verify.sh` only prints.
- `FINAL` fails in place (`CANNOT_SEEK_THROUGH_FILE`, "Seek in compressed archive is not supported")
  when the table has parts whose key ranges overlap. `verify.sh` counts the Aggregating, Replacing,
  Summing and Collapsing engines with `FINAL` (a merge changes `count()`), so those tables are
  restored into the scratch database and counted there, by the same `ch_row_count`. Cost: their size
  in disk until `backup.sh` ends (both scratch databases and the staged zip are dropped on any exit).
  The rollups are the smaller tables; `spans`, `logs` and the metrics stay zero-copy.

A live data table the archive does not give us (a new engine nobody taught `common.sh` about, or a
table created mid-backup) fails the backup, naming it, rather than leaving a count out of the
manifest. A count that errors fails the backup too: the first version of this fix recorded an empty
`ch.detection_readings=` because an error inside `$( )` was swallowed, which would have made
`verify.sh` fail on every restore.

Rejected: restoring every table into a scratch database (exact, and proves the restore, but doubles
the disk for the whole store on every daily backup); grouping by sorting key instead of `FINAL`
(not the same number for Summing and Collapsing engines, and not what `verify.sh` runs).

**Regression test.** `test_each_clickhouse_count_is_the_rows_the_backup_holds` in
`test_backup_manifest_live.py`. The pass-through `docker` writes a row to a MergeTree table and a
Replacing table and a migration row the instant `BACKUP DATABASE` returns; the archive is restored
into a database of the test's own as the oracle. Seen failing on the original scripts:
`manifest["ch.events"]` was `'2'` (the live count), the archive held `1`. Seen failing on the
attach-only first version (mutated so no table is restored): `backup.sh` exits 1 with
`CANNOT_SEEK_THROUGH_FILE`. It passes with the fix. The Replacing table's parts are kept unmerged
(`SYSTEM STOP MERGES`) with overlapping key ranges, because background merges would otherwise hide
the failure.

Validation of the second fix (own containers, ClickHouse 26.3.20, Postgres 18, migrated to head): a
round trip on the real schema (`detection_readings` with 30,000 rows over unmerged overlapping parts,
`stored_payload_digests`, `schema_migrations`): manifest `ch.detection_readings=30000`, drop the
database, `restore-clickhouse.sh`, `verify.sh`: `PASS`. `test_backup_manifest_live.py` 2 passed;
`test_fix_backup_scripts.py`, `test_backup_retention.py`, `test_install_script.py`: 33 passed; shellcheck
v0.11.0 over `lint-deploy`'s backup files: clean; ruff check and format: clean. Not run: the
`insight-fence` job, `test_backups_live.py` (bucket half, no SeaweedFS here), `mypy .`.

## Status

Implemented, not yet seen in CI. See `status.md`.
