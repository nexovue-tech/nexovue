# Bug: The setup-wait test counted the connection's opening checks as the wait

## Reported behavior

CI run 42464278549 on draft PR #25 (commit `ea2f673`), step "Run just check": 1 failed, 10,209 passed.

```
apps/api/tests/test_setup_live.py::test_a_setup_waiting_for_the_migration_lock_holds_no_transaction_or_statement
test_setup_live.py:217: assert blocked == []
AssertionError: Left contains one more item: {'state': 'idle in transaction', 'wait_event_type': 'Client', 'wait_event': 'ClientRead'}
```

No setup or `migrations/env.py` code changed between the last green run (`de81f12`) and `ea2f673`.

## Reproduction

The test passed 10 of 10 alone. Under load it fails. With Postgres and SeaweedFS on 15432 and 18333,
four CPU-burning processes (`python3 -c "while True: pass"`), and six copies of the test at once, five
rounds:

```
python3 load.py 6 5 4      # scratch script: 6 parallel pytest runs x 5 rounds, 4 CPU burners
FAIL 1 1
runs 30 fails 1
E         Left contains one more item: {'state': 'idle in transaction', 'wait_event_type': 'Client', 'wait_event': 'ClientRead'}
```

A copy of the sampler that also read `pid`, `query`, `backend_start` and `xact_start` caught it
twice in 40 runs under the same load. Each time there was one setup session, and it was 0.1-0.4 s
after that session connected:

```
'state': 'idle in transaction', 'query': 'BEGIN;'
'state': 'idle in transaction', 'query': 'select current_schema()'
```

With `log_statement = 'all'` set on one scratch database, the setup's session sent exactly this:

```
statement: BEGIN;
execute __asyncpg_stmt_1__: select pg_catalog.version()
execute __asyncpg_stmt_2__: select current_schema()
execute __asyncpg_stmt_3__: show transaction isolation level
execute __asyncpg_stmt_4__: show standard_conforming_strings
statement: ROLLBACK;
execute __asyncpg_stmt_5__: SELECT pg_try_advisory_lock(7954896736943957250)   (every 0.5 s)
```

## Root cause

The test sampled the wrong span of time. `_waiting_setup_samples` in
`apps/api/tests/test_setup_live.py` (line 164 before the fix) counted every sample of the setup's
session from the moment it connected. But the first thing a SQLAlchemy engine does on its first
connection is check the server: `first_connect` in `sqlalchemy/engine/create.py` calls
`dialect.initialize`. With the asyncpg driver, that opens a transaction, runs the four statements
above, and rolls back. Between those statements the session is briefly `idle in transaction`. That
happens before `wait_for_migration_lock` in `apps/api/migrations/env.py` (line 64) runs its first
`pg_try_advisory_lock`. It usually lasts about 3 ms, so a sampler that reads every 50 ms rarely
catches it. On a busy CI runner the Python process is slow between statements, the gap gets longer,
and sometimes a sample lands in it. Nothing in setup changed. The test was always able to fail this
way.

**This is a defect in the test (b), not a window in setup (a).** A waiter only deadlocks a
`CREATE INDEX CONCURRENTLY` if it also waits for the holder while it holds that state. The check
transaction waits for nothing. It reads no table, takes no lock the holder could hold, and the client
sends `ROLLBACK` straight after it unconditionally. Measured with a throwaway script (not in the repository), the holder holding
`MIGRATION_LOCK` builds an index concurrently while a second session is:

```
A (the check transaction, held open 2 s, then polling):  CIC finished after 0.00s | waiter got lock
B (blocked in pg_advisory_lock, the hazard):             CIC finished after 0.70s | waiter: DeadlockDetectedError: deadlock detected
```

B shows the experiment can see the real hazard. A shows the check transaction does not even delay
the build.

Ruled out:

- **Something changed in setup:** no change between `de81f12` and `ea2f673`. Migration `0053` is an
  `op.create_table`, and it runs only after the lock is taken, which is after sampling ends.
- **A second setup connection, or a pool ping:** every caught sample had exactly one session in the
  scratch database. `NullPool` has no ping configured.
- **The poll itself in a transaction:** the statement log shows every `pg_try_advisory_lock` outside
  a transaction (`isolation_level="AUTOCOMMIT"`). SQLAlchemy's pretend transaction around the poll
  is never sent to the server.
- **The moment after the lock is taken** (Alembic's `BEGIN`): the holder keeps the lock until
  sampling ends, so the setup cannot get there while it is sampled.

## Customer impact

None today: nexovue is pre-deployment, and the setup's behaviour was already correct. The cost was
to the gate. A correct change could fail `just check` at random, and that teaches people to re-run
it until it passes, which hides real failures.

## Fix

The sampler now starts counting when a session first calls the lock function (`advisory_lock`,
found in `pg_try_advisory_lock` and `pg_advisory_lock`). From then on it samples every session in
the scratch database except the holder for `SETUP_WAIT_SAMPLED_SECONDS`, not just the session that
asked. That keeps the old coverage of a second setup connection left idle in a transaction. The
setup may take up to `SETUP_START_SECONDS` (60 s) to start and ask, instead of having to fit into
the old fixed 4 s. If it never asks, the test fails ("the setup was never seen waiting for the
lock"). It does not pass quietly.

The guarantee still holds. A waiter that opens a transaction before it asks is still caught while
it polls, because the transaction stays open. A setup that got stuck behind the holder before ever
asking would never ask, and fails the same way.

## Specification impact

None.

## Regression test

In `apps/api/tests/test_setup_live.py`, using a stand-in waiter on its own asyncpg connection:

- `test_the_server_checks_on_connecting_are_not_counted_as_the_wait`: the stand-in holds a check
  transaction like SQLAlchemy's open for 1 s, then polls. This reproduces the CI window every time.
- `test_a_waiter_polling_inside_a_transaction_is_caught` and
  `test_a_waiter_blocked_in_pg_advisory_lock_is_caught`: they prove the guarantee was not weakened.

Seen failing with only the sampler reverted to the original:

```
E       AssertionError: assert [{'state': 'i...hema()'}, ...] == []
E         Left contains 20 more items, first extra item: {'state': 'idle in transaction', 'wait_event_type': 'Client', 'wait_event': 'ClientRead', 'query': 'SELECT current_schema()'}
1 failed, 2 passed, 4 deselected
```

The two "is caught" tests passed against the original sampler too, as they should. With the fix,
all of `test_setup_live.py` and `test_migration_rules.py` pass: 31 passed.
