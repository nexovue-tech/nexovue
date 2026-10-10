# Status: redis-goes

## Lifecycle status

Implemented and verified on the development stack. Not committed.

## Current task

None.

## Completed

- Research and founder decisions recorded in `change.md`.
- Design approved, 2026-10-10 (`d121fc7`); plan approved (`3cd5bf4`).
- **Step 1 — login state in Postgres.** Migration `0037` (ten tables; up, down, up clean).
  Sessions, one-time links, second-factor state and view-as tickets on Postgres. Erasure
  classifies every new table (`test_deletion_live.py` red on four unreached `org_id` tables,
  then 24 passed). Red then green: a reset spends every other reset link
  (`revoke_all_for`, newly possible); two racing spends of one link cannot both win
  (red with a read-then-delete); revoking another user's session refused (red without the
  user predicate); fifth wrong code ends the challenge and a code claims once (red with
  each rule broken). Live on the dev API: signed in, the session row is in Postgres,
  deleting every Redis `session*` key leaves `/auth/me` at 200, sign-out gives 401.
- **Step 2 — the API stops counting in Redis.** Read tiers and in-flight caps in process,
  divided by `API_INSTANCES`; the auth tier exact in Postgres, refusing when it cannot count;
  API tokens held to an organization-wide 32 in flight; token usage counted in memory and
  added once a minute. Red then green: per-process share; two limiters share the auth count;
  refused when Postgres is unreachable; the org ceiling (red as a timeout — the fifth token
  was served); usage adds rather than overwrites. Live on the dev API: ten failed logins 401,
  the eleventh 429, `rate_limit_windows` holds 11, Redis holds no auth key.

- **Step 3 — the jobs table replaces Celery.** `nexovue_platform.jobs` (the contract: shape
  and `ENQUEUE` as SQL text); `nexovue_scheduler.jobs` (claim/lease/finish/retry, spawned child
  per slot with `SIGALRM` soft limit and kill at the hard limit, LISTEN wake-up, expiry sweep);
  advisory-lock leader; beat on its own loop, marking an entry fired only once its job is in the
  table; every task on the registry; results and evaluation status in Postgres; the API adds
  Run now and send-test jobs with `NOTIFY`. `celery_app.py` and the dead `health.py` deleted.
  Red then green: double claim (red without the lease filter, once the test's jobs had attempts
  to spare — with one attempt the attempt guard alone hid it); hard-limit kill (red as a 60 s
  wait); NOTIFY wake (red as a timeout); retry countdown; dead leader learns it lost; result
  read only by the asking organization; a task name no worker registers. Live on the dev stack:
  beat leads, checks complete, all three alert sweeps write `alert_evaluation_status`, the queue
  drains to zero; killing the leading beat → the standby leads 2.3 s later; Run now through the
  API → `complete up` in 1.6 s; send test → the worker's real outcome; killing `scheduler-light`
  → six jobs wait in the table, all run 1 s after it restarts.

- **Step 4 — the receiver counts in process.** `ratelimit.Limiter` is the token bucket alone, at
  `RECEIVER_RATE_LIMIT_PER_SECOND ÷ RECEIVER_INSTANCES`; `valkey.go`, the fallback path, its two
  traffic reasons and `REDIS_URL` gone; `valkey-go` out of `go.mod`. Red then green: the per-receiver
  share. Live: the receiver's `/readyz` checks Kafka and Postgres only, with Redis stopped.
- **Step 5 — Redis is gone.** The `redis` service, volume, port and every `REDIS_URL`,
  `PROBE_BROKER_URL` and `depends_on: redis` removed from all compose files and both `.env.example`;
  `redis`, `celery[redis]` and the Celery mypy overrides out of the Python projects and `uv.lock`.
  Both deployment files render with every required variable. Docs: ADR-011 amended, ADR-020's open
  questions closed, decision-log entry, architecture pages, readiness blocker 15 and group 2b.
- **Found by the full check — the claim over-leased.** `test_each_job_runs_once…` timed out in two of
  three batches of runs; traced: one claim for two free slots returned twenty jobs, the worker raised
  popping a slot it did not have, and died holding eighteen leases. `id IN (SELECT … LIMIT n FOR
  UPDATE SKIP LOCKED)` lets Postgres re-run the subquery under some plans. Now a `MATERIALIZED` CTE;
  twelve consecutive passes, then six full-suite passes. Worker names are unique per worker, not per
  process, so two workers can never share a lease fence. A direct test of the over-claim could not be
  made to fail on the old statement (it depends on the plan), so none was kept.

**Validation (final):** `just check` exit 0 — Python 4209 passed, 3 skipped (the billable mail
suite, and two existing data-shape skips); web 1352; browser SDK 120; ruff, mypy (598 files),
golangci-lint 0 issues across the Go modules.

**Deviations from the plan, and why:**
- Second-factor state is three tables (`totp_enrolments`, `totp_challenges`,
  `totp_spent_codes`), not one keyed `(user_id, kind)`: a challenge is per sign-in, so two
  browsers would have overwritten each other; the spent marker is keyed on the code, as in Redis.
- View-as tickets are their own typed table rather than a purpose of `one_time_tokens`.
- `SecondFactorStore` writes in its own transactions, not the request's: a wrong code answers
  401, which rolls the request back, and the spent attempt must survive that.
- `record_failed_attempt` increments in one statement and no longer takes the challenge.
- T11 (a test proving sessions survive Redis, to be deleted in step 5) was done by hand on the
  live API instead.
- `test_ratelimit_callers_live.py` became `test_ratelimit_callers.py`: its tiers are now in
  memory, so it needs no store.
- A worker that dies re-runs a job only if the job has an attempt left. `record_outcome` (6)
  and the pollers (3) do; a probe run, a sweep or a delivery has one, and the next interval or
  sweep covers it — a probe re-run after its lease lapses would be past its expiry anyway.
  Celery redelivered every unacknowledged task; this is bounded on purpose, and a job lost with
  no attempt left is logged by name when swept.
- The API drops `redis_url`/`probe_broker_url` and its Redis health check in this step rather
  than step 5: nothing in the API used Redis once Run now moved.
- `test_beat_shutdown.py` deleted: it guarded Celery closing its scheduler twice, which no
  longer exists.

## Next action

Founder reviews the diff; on approval, commit. Then readiness group 3 (money).

## Open decisions

None. Both of `plan.md`'s assumptions were built as stated: `jobs.org_id` is nullable and erased
with the organization; the queue's contract is SQL text in `nexovue_platform.jobs`.

## Blocked

None.

## Risks

- `plan.md` New risks stand: fork-free spawned children, the soft limit cannot interrupt C code,
  `API_INSTANCES`/`RECEIVER_INSTANCES` must be set by the deployment (group 6), up to a minute of
  token usage lost on a crash.
- The advisory lock needs a direct Postgres connection; a transaction-mode pooler would break it.
- `probes/tasks.py`'s `_TAKE_UNATTACHED` uses the same `IN (SELECT … LIMIT … SKIP LOCKED)` shape.
  There the cost is only deleting more than 500 unattached files in one batch, so it is left; the
  expiry sweeps' batches are the same and equally harmless.
- The dev machine still holds the stopped `nexovue-redis-1` container and the `redis-data` volume;
  nothing references either.

## Validation

None run yet.

## Specification impact

None.

## Documentation impact

Updated: ADR-007, ADR-010, ADR-011 (amendment), ADR-019, ADR-020 (questions closed), the decision
log, `docs/architecture/` (system, current-system reference, database, backend, api), the readiness
document and the changes index. ADR-013 needs no change: its decision names no broker.

## Last updated

2026-10-10
