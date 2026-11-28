# Status: fix-sweep-records-as-its-account

## Lifecycle status

Verified (uncommitted; the coordinator commits).

## Current task

None.

## Completed

- `apps/api/src/nexovue_api/connections_account.py`: `SELECT (sweep, finished_at)`,
  `INSERT (sweep, finished_at)` and `UPDATE (finished_at)` on `fix_sweep_status` for
  `nexovue_connections`; the docstring's "what a broken-into process can still do" names it.
- `apps/api/tests/test_connections_grants_live.py`: the three rights in the hand-written list.
- `apps/scheduler/tests/test_fix_sweep_status_live.py`: `sweep_and_record` as the account, and
  a test of both arms of the upsert.
- `apps/scheduler/tests/test_fix_summary_live.py`: sweeps as the account, not the install
  account.
- `apps/scheduler/src/nexovue_scheduler/connections/fix_sweep.py`: two comments named migration
  0085 for `fix_sweep_status`; it is 0086.
- ADR-030's reach statement; notes in F22's and F5's `status.md`.

## Next action

The coordinator reviews and commits this change with the batch; after the merge, `just migrate`
(or `nexovue setup grants`) on the dev stack applies the grant, and `scheduler-connections`
should stop logging `permission denied for table fix_sweep_status`.

## Open decisions

None. (The `SECURITY DEFINER` alternative was considered and not chosen; see `bug.md`.)

## Blocked

Nothing.

## Risks

- A grant list in code and a table in a migration can drift again when two branches meet. What
  catches it is a suite that runs the job as the account; any new statement on this role needs
  its test on `connections_engine`, not the install engine.
- The account can set any time as the sweep's last finish, so a broken-into tracker process can
  keep `NexovueSuggestionSweepStalled` quiet. Stated in ADR-030; no grant can prevent it.

## Validation

Run on this change's own Postgres (`grant-postgres`, 127.0.0.1:35450), migrated to 0086 by the
real `nexovue_api.setup`, removed afterwards.

- Seen failing before the fix: 3 failed, 11 passed (`test_fix_sweep_status_live.py` two tests,
  "permission denied for table fix_sweep_status"; `test_connections_grants_live.py`, the three
  rights missing). After: 14 passed.
- Every connections and F5 live suite that runs these jobs (sync, GitHub, cases sweep, account,
  fix access-gone, app, check, general sweep, patch check, pull request state, never-leaks,
  source pack, summary, sweep, sweep records, sweep status, write hostile, writer) plus the
  grants suite: 273 passed.
- Grants (connections, insight, fix insight), migration rules, Postgres and scheduler tenancy
  structure, tenant chokepoint and scope, setup schema, connections account, F5's sweep suites:
  3118 passed. `test_setup_live.py` not run: it needs an S3 store (4 errors, "Postgres or the
  S3 store is not reachable"); the setup code is unchanged.
- `uv run mypy .`: no issues in 1352 source files. `ruff check`: all passed.
  `ruff format --check`: 1787 files already formatted.
- Not run: `just check` as a whole, and `test_connections_end_to_end_live.py` (the real worker;
  beat, which schedules the sweep, is not part of it).

## Specification impact

None.

## Documentation impact

ADR-030 (reach), `connections_account.py`'s docstring, F22 and F5 `status.md` notes. The two
stale "migration 0085" comments in `fix_sweep.py` corrected.

## Last updated

2026-11-22
