# Status: request holds one connection

## Lifecycle status

Implemented — awaiting the founder's review of the diff. Not committed.

## Current task

None; handed back for review.

## Completed

- **Reproduced** at 20 viewers on one API process (9 and 22 HTTP 500 in two runs, 15 connections
  `idle in transaction` for 10 s) and in one request on a pool of one ([`bug.md`](bug.md)).
- **Root cause found**: the caller's lookup left its transaction open, so every request held its
  connection to the end; a route asking for a person and a reader then took a second connection
  while holding the first, and a full pool deadlocked until its timeout, answered as 500.
- **Fixed** ([ADR-102](../../decisions/ADR-102-a-request-gives-back-its-postgres-connection-once-its-caller-is-known.md)):
  - `apps/api/src/nexovue_api/auth/dependencies.py`: `_release_connection` ends the lookup's
    transaction (a commit) at the end of `get_current_identity` and of `get_current_reader`'s token
    and ticket branches (the ticket one also serves `get_ticket_reader`). The person branch goes
    through `get_current_identity`.
  - `apps/api/src/nexovue_api/errors/problems.py`: problem type `service-busy` (503).
  - `apps/api/src/nexovue_api/errors/handlers.py`: a pool timeout (`sqlalchemy.exc.TimeoutError`)
    answers 503 `service-busy` with `Retry-After: 2` (`BUSY_RETRY_AFTER_SECONDS`), from
    `handle_unexpected_error`, which both FastAPI's handler and the outermost middleware call; logged
    as a warning `postgres pool exhausted`.
  - `apps/api/tests/test_tenant_isolation.py`: its fake Postgres gains a no-op `commit`, the one
    method the lookup now calls.
- **Regression tests**: `apps/api/tests/test_request_connection_live.py` (5) and
  `test_errors.py::test_a_full_pool_met_in_middleware_answers_busy`, seen red and then green.
- **Docs**: ADR-102 and its index line; `docs/engineering/performance.md` (a request holds a
  connection only while it runs Postgres, and a full pool answers 503); F20 `status.md` decision 40
  points here; `docs/changes/README.md`.

## Next action

The founder reviews the diff and the open decisions below; on approval the coordinator commits it.
Then F20's T5.7 is rerun with ingest and the worker on (this change's rerun had none).

## Open decisions

1. **A route still holds its connection from its own first Postgres read to its end** — through its
   ClickHouse reads, which follow the project check on every telemetry route (`traces`, `services`,
   `metrics` and the other routers that read telemetry, 19 in all). This is no deadlock (no second connection is needed) but it ties a
   pool slot to ClickHouse time; at 20 viewers the samples peaked at 6 `idle in transaction` for an
   instant, against 15 before. Options: (a) leave it, now that a full pool answers 503; (b) end a
   read-only transaction at the tenancy chokepoint before each ClickHouse read (`TenantScopedRepository`),
   which would also commit the 13 write routes that read telemetry part-way (the findings actions,
   the source-fix asks, the dashboard preview) and so change what a failed request leaves behind —
   each would need its own reading; (c) commit before the scans per service, as the dashboard read
   already does. **Recommendation: (a) now, and (c) for any route a measurement shows holding long.**
2. **Other places that take a second connection while holding the request's**, each brief:
   `checkruns` and `channels` dispatch (the check or channel read, then `add_job` on a session of its
   own), `profile` ending a session (the request's session, then `SessionStore`), and turning a second
   factor off (`SecondFactorStore`). Under a full pool each can still wait, and now answers 503 rather
   than 500. Run now's and Send test's *waits* (up to 10 s) no longer hold a connection, because the
   lookup was the only Postgres work before them. **Recommendation: leave; fold into (1) if it is
   taken up.**
3. **The numbers**: `Retry-After: 2` and the pool's 10-second wait (`POSTGRES_POOL_TIMEOUT_SECONDS`)
   are proposals. A busy answer arrives only after that wait; a shorter one answers sooner and refuses
   more under a burst. **Recommendation: keep both until T5.7's rerun on Oracle.**
4. **A person-and-reader route still looks the caller up twice** (`get_current_reader` calls
   `get_current_session` and `get_current_identity` directly, so FastAPI's per-request cache does not
   apply): two more Postgres reads per dashboard read, one connection at a time now. Removing it means
   a cache of the resolved caller on the request. **Recommendation: a separate small change.**

## Blocked

Nothing.

## Risks

- The fix relies on the caller's lookup taking no lock and setting nothing transaction-local, so that
  ending its transaction loses nothing (true today: plain `SELECT`s, and the support view's audit line
  is committed already). A lookup that must hold a lock into the route would break it; ADR-102 says so.
- The load rerun had **no ingest and no worker** (a stub answered the tool's receiver and metrics URLs),
  on a shared 4 vCPU VM: the comparison is like for like, the absolute numbers are indicative only.

## Validation

All on this change's own stores: `pool-postgres` (127.0.0.1:35437) and `pool-clickhouse` (HTTP 38128,
native 39008), migrated (Postgres, then ClickHouse's 42; the object store's bucket step failed after the
migrations, as no object store was started), `just seed`, `tools/dashboard-demo/demo.py --run`
(144,139 rollup rows, 15 services); for `test_tenant_isolation_live.py` also `pool-kafka`
(127.0.0.1:39093), topics, and a receiver and worker built from this worktree, with 2,000 spans sent as
the seeded organization. Settings in a private file outside the repository. Containers removed at the end.

**Regression, before the fix** (constants present, behaviour not): 4 failed, 1 passed —

```
E   AssertionError: [('idle in transaction', 'SELECT users.id, users.org_id, ... organizations.deleted_at IS NULL')]
E   assert 500 == 200
E   assert [500, 500, 500, 500, 500, 500] == [200, 200, 200, 200, 200, 200]
E   assert 500 == 503
```

and `test_errors.py -k full_pool` with the mapping disabled: `assert 500 == 503`, 1 failed.
**After**: `test_request_connection_live.py` 5 passed; `test_errors.py` 14 passed.

**Load** (`tools/dashboard-load/load.py --people 20 --levels 1 5 20`, 3 rounds, one API process,
`API_INSTANCES=1`, pool 10 + 5 with a 10 s timeout). At 20 dashboards at once:

| Run | batch p50 | batch p95 | panels | HTTP errors | `idle in transaction`, peak |
|---|---|---|---|---|---|
| before, 1 | 2,286 ms | 10,867 ms | 686 ok, 182 refused | 500 x 9, 429 x 20 | 15 (for 10 s) |
| before, 2 | 1,369 ms | 11,334 ms | 716 ok, 348 refused | 500 x 22 | 15 |
| after, 1 | 1,873 ms | 2,018 ms | 906 ok, 214 refused | 429 x 20 | 6 (an instant) |
| after, 2 | 1,939 ms | 2,369 ms | 910 ok, 210 refused | 429 x 20 | 1 |

At 1 dashboard p95 was 300 and 233 ms before, 222 and 283 ms after; at 5, 937 and 865 ms before, 883
and 1,099 ms after, all panels `ok` throughout. API peak resident 262 to 267 MiB in every run. No
`QueuePool` line and no traceback in either after-run's log. The 429s and refused panels are the
per-person and per-process caps working as designed (the measurement's finding 2), now reached because
the reads get that far.

**Suites** (`uv run pytest -n 4 --dist loadfile`, live suites against the stores above): the
tenancy, chokepoint, isolation (offline and live), authorization coverage, policy, session store,
commit order, auth routes, read surface, API tokens, view-as, personal data, assistant tickets, admin
mode, second factor, profile, members and all dashboards suites, 36 files: **4099 passed in 291.76s**,
none failed or skipped. A first run of the same list gave 12 failures in `test_tenant_isolation.py` (its fake Postgres
had no `commit`; fixed) and 4 errors in `test_tenant_isolation_live.py` (no receiver yet; it then ran,
9 passed). The 15 other files that replace the request's session (`test_apikeys`, `test_audit`,
`test_billing`, `test_projects`, `test_signup`, the fix routes and others): 635 passed.

`uv run mypy .`: 6 errors, all `import-not-found` for `dns` in `apps/scheduler` (dnspython is not
installed in this worktree's environment; the same 6 at the base); none in the API.
`uv run ruff check apps/api`: all checks passed. `uv run ruff format --check apps/api`: all formatted.
`just check` not run (by instruction).

**Self-review** (`/review-diff`): every changed file is explained above; no query, tenant predicate,
role gate or ClickHouse statement changed; `org_id` still comes only from the session. One finding,
fixed: `_release_connection`'s docstring was longer than the comment standard allows, shortened.
Nothing open beyond the decisions above.

## Specification impact

None.

## Documentation impact

ADR-102 added and indexed. `docs/engineering/performance.md` gains the rule. F20 `status.md` decision
40 points here. `docs/research/f20-measurement-2026-11-22.md` finding 1 is left as the record of what was
measured; its guess at the cause ("on a single event loop that is busy planning") is superseded by
`bug.md` here, which F20's decision 40 now says.

## Last updated

2026-11-22.
