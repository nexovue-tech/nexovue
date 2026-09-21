# Change: faster-gate — the pre-push loop, measured and shortened

## Classification

Infrastructure (test execution; no product behaviour changes).

## Current behavior and problem

Measured on the founder's machine, 2026-09-20, with a warm cache and a loaded dev stack:

| | Time |
|---|---|
| `just check` | 284s — of which **`test-py` 248s**; Go 21s, web 9s, SDK 2s, lint and format 10s |
| `just e2e` | 186s |
| **Both** | **470s** |

Two measurements explain nearly all of it.

**The Python suite is I/O, not computation.** 196 of 246 timed seconds — 80% — are in `*_live.py`
suites talking to the real Postgres, ClickHouse, Redis and Kafka. Collection of all 4117 tests takes
2s. Two files are 37% of the total on their own: `test_purge_live.py` (57s) and
`test_deletion_live.py` (34s), and **46s of that 91s is teardown**, waiting on ClickHouse mutations
that nothing afterwards reads.

**The e2e suite is rate-limited, not browser-bound.** It already runs four browsers in parallel.
Signup and login are capped at ten a minute per client address; every browser reaches the API
through one proxy, so all four workers share one bucket, and the harness paces itself against it
(`tools/e2e/tests/support/auth-quota.ts`). 37 auth call sites across 12 specs against an allowance
of 8/minute is where the three minutes goes: `sign-in` takes 43s and `a wrong password` 42s while
doing almost no work.

## Intended behavior

Three changes, each measured before and after, none of which weakens what a suite proves:

1. **A teardown that deletes stops waiting to watch it happen.** `tests/helpers/clickhouse_cleanup.py`
   already exists for exactly this — it records a test tenant and issues one mutation per table at
   the end of the session, without waiting. The purge and deletion fixtures purge synchronously
   instead. The tests that *prove* a purge works keep calling it in their own body and keep waiting;
   only the cleanup defers.
2. **The Python suite runs in parallel.** It is 80% waiting on stores, which is what `pytest-xdist`
   is for. Distribution by file, so a file's tests keep sharing their fixtures and two workers never
   purge inside one suite at once.
3. **The e2e suite spends fewer auth requests.** What most specs need is an empty *project*, not a
   new *organization* — and creating a project is not on the auth tier. A worker signs up once and
   its specs take a fresh project each; the specs that genuinely exercise signup, login or two
   tenants keep their own accounts.

## Affected specifications and architecture

None. No product code changes; the rate limit, the purge and every assertion stay as they are.

## Risks and rollback

- **Parallelism is where the risk is.** Suites that share one database can interleave — migrations,
  leader election, anything counting rows it did not scope to itself. Each is checked before the
  flag goes in, and the gate is run repeatedly to catch order dependence rather than assumed clean.
- **A deferred cleanup leaves rows until the session ends.** They belong to a UUID tenant nothing
  else reads, which is the same argument `clickhouse_cleanup` already makes.
- **Fewer e2e accounts means more specs sharing one organization.** A spec that leaks state into a
  shared org would now be seen by its neighbours — which is why each takes its own project.
- Rollback is `git revert`; nothing here touches a store or a schema.

## Plan and validation

[plan.md](plan.md); the before/after measurements are in [status.md](status.md).

## Status

In development.
