# Bug: a request held its Postgres connection to the end, and needed a second one

## Reported behavior

F20's measurement (T5.7, 2026-11-22, [`f20-measurement-2026-11-22.md`](../../research/f20-measurement-2026-11-22.md),
finding 1; F20 open decision 40): twenty people reading dashboards at once against **one** API
process gave HTTP 500 on 7 of 60 reads, logged as `QueuePool limit of size 10 overflow 5 reached,
connection timed out`. Sampled during the run, `pg_stat_activity` showed all 15 pooled connections
`idle in transaction`, the last statement of each the identity lookup (`SELECT users...`).

## Reproduction

**The measurement, on this change's own stores** (Postgres `pool-postgres` 127.0.0.1:35437,
ClickHouse `pool-clickhouse` 127.0.0.1:38128, migrated, seeded with `just seed` and
`tools/dashboard-demo/demo.py --run`; one API process from this worktree on 127.0.0.1:38012,
`API_INSTANCES=1`, pool 10 + 5, timeout 10 s; `tools/dashboard-load/load.py --people 20 --levels 1 5 20`).
At 20 dashboards at once:

```
before   batch p50 2,286 ms  p95 10,867 ms   686 ok, 182 refused   HTTP 500 x 9, 429 x 20
```

and `pg_stat_activity`, sampled once a second (`state:count` of the API's backends):

```
08:26:25 idle in transaction:15
...                                  (every sample for 10 s)
08:26:34 idle in transaction:15
```

Of the nine tracebacks, seven end in `auth/sessions.py` `_session_by_token_hash` and two in
`auth/queries.py` `load_identity`, each `sqlalchemy.exc.TimeoutError: QueuePool limit ... timeout
10.00`. Fifteen connections held for a full ten seconds with nothing running is not a busy event
loop: the holders were waiting on something that could not happen.

**In one request.** On a pool of **one** connection, a route that asks for a person and a reader
(the dashboard read's shape) answered 500 on the very first request; a route that asks for a person
and then waits showed its connection `idle in transaction` on the identity lookup throughout the wait
(the regression suite below, run before the fix):

```
E   AssertionError: [('idle in transaction', 'SELECT users.id, users.org_id, ... WHERE users.id = $1::UUID
      AND users.org_id = $2::UUID AND organizations.deleted_at IS NULL')]
E   assert ['idle in transaction'] == ['idle']
...
E   assert 500 == 200        (a person and a reader, pool of one)
E   assert [500, 500, 500, 500, 500, 500] == [200, 200, 200, 200, 200, 200]   (6 at once, pool of two)
E   assert 500 == 503        (every connection taken)
4 failed, 1 passed
```

Ruled out, each by a reading: **ClickHouse work holding the connection** — the dashboard read already
commits before its scans (`dashboardpanels/service.py`, "let go of the connection before ClickHouse"),
and the held connections' last statement was the identity lookup, not the read's own Postgres reads
that come after it; **the rate limiter's wait** — it runs in middleware *before* the route's
dependencies (`middleware/ratelimit.py` `_serve` waits for its slot, then calls the app), so it holds
no request connection; **a CPU-bound event loop** — every sample for ten seconds showed fifteen
holders and nothing `active`, where a busy loop would still let some progress; **the pool's size** —
the deadlock needs one request on a pool of one.

## Root cause

Two faults, the first making the second deadly. Line numbers are at `4c9cbd8`.

1. **The caller's lookup left its transaction open.** `get_current_identity`
   (`apps/api/src/nexovue_api/auth/dependencies.py:83`, the lookup at line 95) runs on the request's
   session (`deps.get_db_session`, `deps.py:68`), which takes a pooled connection on its first
   statement and keeps it until the transaction ends — and nothing ended it before the route's last
   line. So every authenticated request held a connection from the lookup to its response, idle
   through every non-Postgres wait.
2. **A request then asked for a second connection while holding the first.** `get_current_reader`
   (`auth/dependencies.py:259`) looks the session up again (`get_current_session`, line 280), and
   `SessionStore.get` reads on a session of its own (`auth/sessions.py:265`). A route asking for both
   a person and a reader — the dashboard read (`dashboardpanels/router.py:56`, `CurrentIdentity` and
   `TenantRepository`) — resolves the person first, so it holds the lookup's connection and waits for
   another. Fifteen such requests take all fifteen connections and each waits for a sixteenth: nobody
   moves until the pool's 10-second timeout.

And the timeout reached the caller as **500**, because `sqlalchemy.exc.TimeoutError` had no mapping in
`errors/handlers.py` and fell to the unexpected-error handler.

## Customer impact

None today: pre-deployment. For a customer: an incident is exactly when a team opens the same screens
together, and twenty people on one process (the small install's shape) would see screens fail with "the
request could not be completed" for ten seconds at a time, on any route that resolves a person and a
reader — the dashboards, and every read-surface route a person opens beside its own person check. Below
that, every request held a connection for its whole length, so a handful of slow routes (Run now and
Send test wait up to 10 s polling with a second connection each) used the pool far beyond their Postgres
work. After the first customer this would be an incident on the busiest day.

## Specification impact

None. F20's NFRs are what the measurement judged; the code now meets them at this level (below).

## Regression test

`apps/api/tests/test_request_connection_live.py` (real Postgres, an engine named per run so
`pg_stat_activity` shows only its connections):

- a slow step after the caller lookup sees its connection `idle`, not `idle in transaction`, and none
  checked out;
- a person and a reader answer 200 on a pool of **one** connection;
- six at once on a pool of two, each pausing after its lookup, all answer 200;
- every connection taken: 503, type `service-busy`, `Retry-After: 2`, no driver text;
- a route that changes `identity.user` still has it saved (the fix must not detach the person).

`apps/api/tests/test_errors.py::test_a_full_pool_met_in_middleware_answers_busy`: the same 503 when the
pool is met in middleware (the rate limiter reads sessions before any route).

Seen red before the fix (above; the middleware test `assert 500 == 503`), green after (`status.md`).

## Fix plan and validation

The design is [ADR-102](../../decisions/ADR-102-a-request-gives-back-its-postgres-connection-once-its-caller-is-known.md):
the caller's lookup ends its own transaction before it returns (a commit, so the person stays loaded
and attached), and a full pool answers 503 `service-busy` with `Retry-After`. Validation and the load
numbers are in [`status.md`](status.md).
