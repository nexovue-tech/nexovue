# Bug: F5's sweep cannot record its finish as the tracker account

## Reported behavior

On the dev stack, `scheduler-connections` fails every minute with
`permission denied for table fix_sweep_status`. The task is `fixes.sweep_fixes`, rung 3's sweep
(`apps/scheduler/src/nexovue_scheduler/connections/fix_sweep.py`).

## Reproduction

On a Postgres of this change's own (`pgvector/pgvector:0.8.6-pg18-trixie`), migrated to 0086 and
provisioned by the real `python -m nexovue_api.setup` (both role accounts ready), the sweep's own
statement as the account:

```
SET ROLE nexovue_connections;
INSERT INTO fix_sweep_status (sweep, finished_at) VALUES ('sweep_fixes', now())
  ON CONFLICT (sweep) DO UPDATE SET finished_at = EXCLUDED.finished_at;
ERROR:  permission denied for table fix_sweep_status
```

The regression tests below, written before the fix, run `sweep_and_record` signed in as the
account (`connections_engine`):

```
E   asyncpg.exceptions.InsufficientPrivilegeError: permission denied for table fix_sweep_status
FAILED apps/scheduler/tests/test_fix_sweep_status_live.py::test_a_whole_pass_records_when_it_finished
FAILED apps/scheduler/tests/test_fix_sweep_status_live.py::test_a_pass_records_again_over_its_last_finish
FAILED apps/api/tests/test_connections_grants_live.py::test_the_account_holds_exactly_its_list
E         Right contains 3 more items:
E         {('fix_sweep_status', 'INSERT'): {'finished_at', 'sweep'},
E          ('fix_sweep_status', 'SELECT'): {'finished_at', 'sweep'},
E          ('fix_sweep_status', 'UPDATE'): {'finished_at'}}
3 failed, 11 passed in 3.29s
```

## Root cause

Two branches built in parallel on 2026-11-22. F22 Step 8 (T8.7) gave `scheduler-connections`
its own Postgres account, `nexovue_connections`, whose rights are exactly
`CONNECTIONS_GRANTS` in `apps/api/src/nexovue_api/connections_account.py`, applied by `setup` and
`nexovue setup grants`. F5 Step 9 (T9.1) added the table `fix_sweep_status` (migration 0086) and
made the sweep's task write it once a whole pass finishes (`sweep_and_record`,
`_RECORD_FINISHED`). Neither branch saw the other: `CONNECTIONS_GRANTS` names nothing on
`fix_sweep_status`, and `test_fix_sweep_status_live.py` ran `sweep_and_record` as the install
(owner) account, so no test signed in as the account that runs it in production.

**Ruled out, by reading:** the pass itself is not refused. `sweep_and_record` runs `sweep()` to
its end, each step in its own committed transaction, and only then writes `fix_sweep_status` in
a transaction of its own; the 273 connections and F5 sweep tests that run the real pass as the
account pass. So no stuck row, stored-code deletion, expiry, refresh or access-gone step is
skipped; only the record of the finish is lost, and the job then fails.

**Other gaps looked for, none found.** Every table named by a statement in the connections task
set (`registry.TASK_SETS["connections"]`: `tasks`, `source_pack`, `fix_app`, `fix_link`,
`fix_sweep`, `fix_check`, `fix_writer`, `fix_poller`, and the shared SQL they import from
`nexovue_platform.cases`, `nexovue_platform.fixes.statements` and `jobs/queue.py`,
`jobs/results.py`) is in `CONNECTIONS_GRANTS`, except `fix_sweep_status`; `job_results` and
`jobs` are reached only through 0085's functions. Of today's other commits after F22 Step 8,
F5 Step 9's only change on this role is this one; F19's and F5's `metrics.py` run in beat on
the install account; F22 T8.8's `evidence/explained.py` runs on the AI role. Column-level
enough-ness is proved by running every connections suite as the account (below).
`test_fix_summary_live.py` swept as the install account too (its docstring said "as beat runs
it", which is wrong: the sweep runs on `scheduler-connections`); it now sweeps as the account
and passes, so it hid no other gap.

## Specification impact

None. F5's design already puts the sweep and its alert on `scheduler-connections`.

## Customer impact

None today (pre-deployment). On an install: the sweep's work would still be done, but
`fix_sweep_status` would never hold a row, so `NexovueSuggestionSweepStalled` would read age
+Inf and fire five minutes after beat starts and never clear, and the connections worker would
log a failed job every minute. A permanently firing alert teaches the operator to ignore it, so
a sweep that really stopped (stored code no longer deleted on time, BR9) would go unnoticed.

## Regression test

- `apps/scheduler/tests/test_fix_sweep_status_live.py`: `sweep_and_record` now runs as
  `connections_engine`, and a new test covers both arms of the upsert as the account (no row,
  then a row moved forward). The pass stays a stand-in, as before, because the real pass crosses
  every organization; the real pass runs as the account in the other sweep suites.
- `apps/api/tests/test_connections_grants_live.py`: the hand-written list holds the three new
  column rights.
- Both seen failing before the fix (above) and passing after.

## Fix plan and validation

The least privilege for the one statement, measured on the account inside rolled-back
transactions: `INSERT (sweep, finished_at)` and `UPDATE (finished_at)` alone are refused, and so
is either with `SELECT` on only one column; the upsert needs `SELECT` on the conflict key
`sweep` and on `finished_at` (read as `EXCLUDED.finished_at`). So the grant is
`SELECT (sweep, finished_at)`, `INSERT (sweep, finished_at)`, `UPDATE (finished_at)`: no
`DELETE`, no change to the key. It goes in `CONNECTIONS_GRANTS`, as every other right of this
account does, applied by `setup` and `nexovue setup grants`; no migration, since F22's pattern
keeps grants in code so a restore ends with them back.

**Considered and not chosen:** a `SECURITY DEFINER` function writing `now()`, so the account
could not set an arbitrary time. It would not stop a broken-into process keeping the alert
quiet (it could call the function without sweeping), and would add a migration for no
protection. The reach is stated in the module's docstring and ADR-030.

## Status

Implemented and verified; see `status.md`.
