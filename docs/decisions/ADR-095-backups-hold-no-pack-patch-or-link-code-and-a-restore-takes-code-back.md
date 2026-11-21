# ADR-095: Backups hold no pack, patch or link code, and a restore takes code back again

## Status

Accepted 2026-11-21, with F5 Step 5 (plan T5.4, T5.5). Builds the F5 design's *Backups* and
*Grants and the checklist* rows. **Awaiting the founder's review with Step 5's diff.**

## Context

`pg_dump` takes every table, and snapshots are kept about 4 weeks (7 daily, 4 weekly). Three F5
tables hold only a customer's code or a sealed code: the files sent for a suggestion, each
person's patch, and a GitHub link attempt's code. And a restore brings back every suggestion's
written code as it was at the snapshot, including code BR9 deleted since, and consents an owner
withdrew since. `verify.sh` compares a restore with the manifest's row counts, so rows left out
of the dump must be left out of the manifest too, or every restore would "fail".

`restore-postgres.sh` is a shell script with no Python and no database driver of F5's, so it
cannot call the statement the rest of F5 uses.

## Decision

1. **The dump excludes those tables' rows** (`--exclude-table-data`, from one list,
   `POSTGRES_ROWS_NOT_KEPT` in `deploy/backup/common.sh`), and the manifest does not count them.
2. **After `pg_restore`, `restore-postgres.sh` runs `deploy/backup/clear-stored-code.sql`** in
   one transaction: work in flight ends `failed`, every stored copy of code goes
   (`code_deleted_reason = 'restored'`), and every consent is withdrawn, so an owner agrees again.
3. **The SQL file is written by code**, `nexovue_platform.fixes.statements.restore_sql()`, from
   the same column and table lists as `clear_stored_code`, and a test in the gate holds the
   file, `common.sh` and the scripts to them (`test_fix_backup_scripts.py`). Changing the list
   without the file fails the build.
4. **The crash window is stated**, not closed: between `pg_restore` committing and the emptying,
   restored code and consents exist. `nexovue restore` has every service stopped and `set -e`
   keeps it from starting them after a failure. Grants stay `setup`'s (F3).
5. **The release check through Docker** (`test_fix_restore_docker.py`) is marked
   `release_check`, left out of every other pytest run, and run by hand per release.

## Alternatives rejected

- **Encrypt the code tables in the dump.** The key would sit beside the dump on the same host,
  and a restore would still bring back code deleted since.
- **Copy the column list into the shell script by hand.** Nothing would notice when F5's list
  grows (Step 7 adds two columns).
- **Re-run the emptying from `setup`.** `setup` also runs on every start, where emptying would
  be wrong; a restore is the only time a snapshot's code returns.

## Consequences

- A backup can be restored without F5's in-flight work or consents: owners agree again.
- `edits` and `files_read` stay in snapshots until they age out, as the consent screen says.
- Step 7's columns join the lists and the file in the same change, or the gate fails.
