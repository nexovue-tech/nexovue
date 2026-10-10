# Plan: Redis goes (readiness group 2b)

> **Status:** Complete 2026-10-10
>
> One document: the order of the work and its tasks. The founder approves this plan before any file is
> edited.

## Preconditions

- Design approved (`design.md`, commit `d121fc7`).
- The dev stack is up (Postgres, Kafka, ClickHouse). Redis stays running until step 5 removes it, so
  each step before that is judged with the rest of the system still on it.

## Current system

Read from the code by three agents and spot-checked; paths under `apps/api/src/nexovue_api/` unless
given in full.

**API stores on `Resources.redis`** (`lifespan.py:112`, built at `:222-227` with the *Postgres* pool size):

- `auth/sessions.py:140` `SessionStore(redis, ttl_seconds)` — `create` :147, `get` :178 (rewrites
  `last_seen_at` after 60 s, :65), `peek` :198 (rate limiter), `list_for` :207, `revoke_one_of` :244,
  `extend` :258 (**no caller**), `set_admin_until` :279, `revoke` :297; `token_fingerprint` :364 is shared
  by tokens, TOTP and tickets. Built at `lifespan.py:257`.
- `auth/tokens.py:82` `OneTimeTokenStore(redis)` — `issue`/`consume` (GETDEL :134), `issue_email_change`/
  `consume_email_change`; `revoke_all_for` :139 **raises `NotImplementedError`** because Redis cannot list
  a user's tokens. Built at `deps.py:175`.
- `auth/totp.py:140` `SecondFactorStore(redis)` — enrolment 600 s, challenge 300 s / 5 attempts, spent
  step 90 s (SET NX, :214). Built at `auth/dependencies.py:286`.
- `support/tickets.py:42` `TicketStore(redis)` — 60 s, GETDEL. Built at `support/router.py:32` and
  `admin/router.py:156`.
- `apitokens/usage.py:48` `count_request(redis, db, …)` — INCR day counter, `token-seen` SET NX once a
  minute folds into `api_token_usage_daily` with `GREATEST`; Redis errors swallowed. Called from
  `auth/dependencies.py:232` and `assistant/auth.py:69`.
- `alerts/evaluation.py:26` `RedisEvaluationReader` — its own client on `probe_broker_url`, built only
  when that is set (`lifespan.py:267-271`).

**Rate limiting.** `ratelimit/limiter.py` (two Lua scripts; `consume` :99, `acquire_slot` :116,
`release_slot` :130); tiers in `ratelimit/policy.py:60-142`; `middleware/ratelimit.py` resolves the
identity (`_signed_in` :181 peeks the session store; auth email from a buffered body :348). **The limiter
has no error handling** — a Redis failure is a 500 from `middleware/errors.py:41`, i.e. it already fails
closed. **API tokens have no organization lookup in the middleware** (`middleware/ratelimit.py:108-112`
says so); `test_ratelimit.py:96` pins `TOKEN_TIER.org_quota is None`. No setting gives the API's instance
count anywhere.

**Scheduler on Celery** (`apps/scheduler/src/nexovue_scheduler/`): `celery_app.py` (queues :51-56, soft
30 s / hard 60 s :61-62, `acks_late` :66, visibility 120 s :70, routes :89-101); 13 tasks across
`probes/tasks.py`, `pollers/tasks.py`, `alerts/tasks.py`, `alerts/sweeps.py`; retries with countdown at
`probes/tasks.py:209` and `pollers/tasks.py:231`; `SoftTimeLimitExceeded` caught at `probes/tasks.py:241`
and `alerts/sweeps.py:101`. Beat is `PostgresScheduler(celery.beat.Scheduler)` (`beat.py:106`), leaning on
Celery's own `tick` for due-ness (:153-178); leader `leader.py` (SET NX PX + two Lua scripts). Results
written to Redis at `probes/tasks.py:423`, `alerts/tasks.py:86`, `alerts/sweeps.py:125`. Postgres access is
async SQLAlchemy on asyncpg, one engine per process after fork (`process_engine.py:23`). `SKIP LOCKED`
with a fenced lease already runs in `alerts/outbox.py:84-206`. No advisory lock anywhere. `health.py`'s
`serve_probes` and `redis_check` have no caller.

**API dispatch.** `checks/run_now.py:41-71` and `channels/send_test.py:39-73` build their own Celery app on
`probe_broker_url` and are `None` without it.

**Receiver.** `ratelimit.Counter` (`internal/ratelimit/ratelimit.go:30`) backed by `valkey.go`; the
in-process token bucket (`fallback.go`) is built at 10× the rate (`:31`) because it is only a stand-in.
Wired at `cmd/receiver/main.go:94-97`; `REDIS_URL` required at `config/config.go:57`.

**Compose.** `redis` (Valkey 8.1.4) at `compose.infra.yaml:153-172`; `REDIS_URL`/`PROBE_BROKER_URL` and
`depends_on: redis` across all three compose files; dev beat runs `replicas: 2` (`docker-compose.yaml:440`).

**Not Redis — must stay:** the customer-peer `redis` in service edges, semconv, the web service map and
onboarding; `checks/targets.py:45` forbidding `redis` as a probe hostname; redaction fixtures.

**Corrections to the design, found here:**

1. **The queue cannot live in `nexovue_platform`.** `test_cross_service_contracts.py:275` forbids
   `sqlalchemy` and `asyncpg` in the shared package. So `nexovue_platform.jobs` holds the *contract* only —
   task and queue names (already there), argument builders (already there), the `jobs` column names and the
   enqueue statement as a SQL string. The API executes that string on its own session; the claim loop,
   child supervision and leader live in the scheduler.
2. **Tables live in the API's Alembic history**, as every Postgres table does; the scheduler reads and
   writes them by SQL, as it does `notification_channels` today.
3. **`revoke_all_for` becomes implementable** (`auth/tokens.py:139`) — Postgres can list a user's tokens.
   Implemented, since the reason it was not is gone.

## Change required

`design.md` (approved). Amends ADR-011, closes ADR-020's open questions.

## Impact

| Area | What changes |
|---|---|
| Files | ~45 source files across API, scheduler, py-platform, receiver; 3 compose files, 2 `.env.example`, justfile comments, 2 pyproject, `go.mod` |
| Services | API, scheduler (all three roles), receiver |
| Schema / migrations | `0037`: 8 tables |
| UI | None |
| Tests | ~25 test files changed; `test_leader_live.py` replaced; new live suites for the stores, queue, leader and auth limit |

## Reuse

- `alerts/outbox.py` lease pattern (claim with `SKIP LOCKED`, fenced outcome on `lease_until`) — the
  `jobs` claim copies it.
- `process_engine.with_engine` — each worker child opens its engine the way Celery's children do now.
- `token_fingerprint` (`auth/sessions.py:364`) — every hashed key.
- `beat.py`'s schedule building, `_unchanged`, `load_enabled_checks`/`load_active_integrations` — kept; only
  dispatch and due-ness change.
- `fallback.go` token bucket — becomes the receiver's limiter.
- `fake_stores.FakeSecondFactorStore` and the five local `FakeSessionStore`s — they fake the interfaces,
  which do not change.
- `run_now_arguments`, `send_test_arguments`, `time_limits_for`, `probe_queue` in py-platform.

## Gaps

- The `nexovue_scheduler.jobs` runtime: claim loop, child pool with soft/hard limits, retry, lease recovery,
  LISTEN wake-up, expiry sweep.
- An in-process limiter for the API with `limit ÷ API_INSTANCES`.
- A Postgres fixed-window counter for the auth tier.
- A per-token organization cache for the token tier's org ceiling.
- An in-process usage counter with a minute flush.

## Steps and tasks

### Step 1 — login state lives in Postgres

*Judgeable:* sign in, see your sessions in Profile, revoke one, enrol and use a second factor, reset a
password, view-as from support — all working; then `FLUSHALL` Redis and you are **still signed in**.

- [x] **T1** — `apps/api/migrations/versions/0037_redis_goes.py` · the 8 tables of design §1 (`jobs` with a
  nullable `org_id`, see Q1), indexes, reasoning in the docstring; downgrade drops them ·
  proved by `just migrate` up/down/up.
- [x] **T2** — `data/postgres/models.py` · ORM models for the 8 tables.
- [x] **T3** — `organizations/tables.py` + `organizations/queries.py` · classify each table: `sessions`,
  `job_results` cascade from organizations; `one_time_tokens`, `second_factor_state`, `totp_spent_steps`
  cascade from users; `jobs` deleted directly (`delete_jobs`); `alert_evaluation_status`,
  `rate_limit_windows` excluded (no tenant) · proved by `test_deletion_live.py` (its classifier tests fail
  red on the unclassified tables first; `_COUNT_BY_ORG`, `FIXTURE_ROWS`, `_write_postgres_rows` extended).
- [x] **T4** — `auth/sessions.py` · `SessionStore(session_factory, ttl_seconds)` on SQL, same interface;
  `extend` deleted · proved by `test_session_store_live.py` rewritten against Postgres (every field
  survives the last-seen rewrite; expired row invisible; `revoke_one_of` refuses another user's session).
- [x] **T5** — `auth/tokens.py` · `OneTimeTokenStore(db)` on the request's session — spending a link
  commits with the work it authorises; `revoke_all_for` implemented · proved by
  `test_auth_recovery_live.py` (two concurrent consumes: one wins; no plaintext in the row — moved here from
  `test_auth_recovery.py`, whose `FakeRedis` goes; `revoke_all_for` red then green).
- [x] **T6** — `auth/totp.py` · `SecondFactorStore(db)` on SQL; the Redis-justifying docstring (:17-37)
  rewritten · proved by `test_second_factor_live.py` (5th failed attempt closes the challenge; a step claims
  once).
- [x] **T7** — `support/tickets.py`, `support/router.py`, `admin/router.py` · `TicketStore(db)` · proved by
  `test_view_as_live.py`.
- [x] **T8** — `lifespan.py`, `deps.py`, `auth/dependencies.py` · stores wired from Postgres; `RedisClient`
  no longer reaches them.
- [x] **T9** — `apps/api/tests/fake_stores.py` · `FakeOneTimeTokenStore` for `test_profile.py:164`, which
  used `test_auth_recovery.FakeRedis` · proved by `test_profile.py`.
- [x] **T10** — `apps/api/tests/test_admin_mode_live.py`, `test_profile_live.py`,
  `test_ratelimit_callers_live.py` · build `SessionStore` from Postgres.
- [x] **T11** — ~~`test_sessions_survive_redis_live.py` (new)~~ done by hand on the live API instead (see Outcome) · sign in over HTTP, flush Redis, `/me` still
  answers · red today, green after T4. (Deleted in step 5 with Redis itself; its job is to prove step 1.)

### Step 2 — the API stops counting in Redis

*Judgeable:* with Redis stopped, the API serves screens and still refuses the 11th login in a minute.

- [x] **T12** — `settings.py` · `api_instances: int = 1` (`API_INSTANCES`), positive.
- [x] **T13** — `ratelimit/limiter.py` · `InProcessLimiter`: sliding window per `(quota, scope, identity)`
  in a bounded map, slots as counters, limits divided by `api_instances` (floor 1); the Lua scripts go ·
  proved by a new `test_ratelimit_limiter.py` (window slides; cap of N; map bounded; slot released).
- [x] **T14** — `ratelimit/auth_window.py` (new) · `consume` as one upsert on `rate_limit_windows`; a
  Postgres failure refuses (429, not 500) · proved by `test_auth_limit_live.py` (11th attempt refused; two
  API processes simulated by two limiters share the count — the thing in-process cannot do).
- [x] **T15** — `middleware/ratelimit.py` · auth tier → T14, others → T13; token tier resolves its
  organization through a 60 s in-process cache over `apitokens/queries.find_by_hash` ·
  `ratelimit/policy.py` `TOKEN_TIER` gains `org_max_concurrent=32` · proved by
  `test_ratelimit_callers_live.py` (new case: two tokens of one org share the ceiling, red then green) and
  `test_ratelimit.py:96` updated.
- [x] **T16** — `apitokens/usage.py` · in-process counts per token, flushed each minute by a lifespan task
  (`count += n`, `last_used_at`), and once more on shutdown · `assistant/auth.py`,
  `auth/dependencies.py:203-232` · proved by `test_read_surface_live.py` (its `_CountingRedis` goes) and
  `test_mcp_live.py`.

### Step 3 — the jobs table replaces Celery

*Judgeable:* checks keep running on schedule; **Run now** returns a result; a test notification sends; kill
`scheduler-light` mid-run and the job runs again on the restarted worker; `docker compose stop` the leading
beat and the other one takes over within 5 s.

- [x] **T17** — `packages/py-platform/src/nexovue_platform/jobs.py` (new) · `JobRequest` (task, queue,
  kwargs, `run_after`, `expires_at`, limits, `org_id`), the enqueue SQL string, `NOTIFY` channel names; no
  driver imported · proved by `test_cross_service_contracts.py` (the import boundary, unchanged).
- [x] **T18** — `apps/scheduler/src/nexovue_scheduler/jobs/` (new): `registry.py` (task name → function,
  `max_attempts`), `worker.py` (claim with `SKIP LOCKED`, LISTEN, child pool), `child.py` (soft limit as
  `SoftTimeLimit` raised by `SIGALRM`, hard limit kills), `retry.py` (`Retry(countdown)`), `expiry.py`
  (sweeps every expired row of design §1 in batches) · proved by `test_jobs_live.py`: claimed once under
  two workers; expired job dropped; soft limit lets the task clean up; hard limit kills and retries;
  a killed worker's job reclaimed after its lease; retry honours countdown; NOTIFY wakes under 1 s.
- [x] **T19** — `probes/tasks.py`, `pollers/tasks.py`, `alerts/tasks.py`, `alerts/sweeps.py` · decorators →
  registry; `self.retry` → `Retry`; `SoftTimeLimitExceeded` → `SoftTimeLimit`; `send_task` → enqueue;
  results → `job_results` / `alert_evaluation_status` upserts; the three `_redis_for_process` go ·
  proved by `test_run_check_task.py` (asserts the enqueued rows instead of `send_task` calls).
- [x] **T20** — `beat.py` · its own loop: due-ness computed from each entry's interval and last enqueue
  (the hash offset kept), enqueues instead of `apply_async`; `leader.py` → `pg_try_advisory_lock` on a
  dedicated connection · proved by `test_beat_schedule.py` (asserts job requests, not Celery options),
  `test_beat_shutdown.py`, and `test_leader_live.py` rewritten: one holder of two; holder's connection
  closed → the other wins on its next poll.
- [x] **T21** — `apps/scheduler/src/nexovue_scheduler/__main__.py` (new) · `python -m nexovue_scheduler
  beat|worker --queues … --exclude-queues … --concurrency N`; `celery_app.py` and `health.py`'s dead
  `serve_probes`/`redis_check` deleted; `Dockerfile` CMD.
- [x] **T22** — `checks/run_now.py`, `channels/send_test.py`, `alerts/evaluation.py` · enqueue on the
  request's session + NOTIFY; results from `job_results`; `PostgresEvaluationReader`; never `None` ·
  proved by `test_cross_service_contracts.py:313-384` (enqueued names and kwargs match the registry —
  red until T18 registers them) and a live Run-now through the API.
- [x] **T23** — compose ×3 · scheduler commands → T21; scheduler-beat keeps `replicas: 2` in dev.

### Step 4 — the receiver counts in process

*Judgeable:* ingest runs with Redis stopped; a flood over the limit is refused with 429.

- [x] **T24** — `internal/ratelimit/` · `Limiter` becomes the token bucket at `perSecond ÷
  RECEIVER_INSTANCES`, burst one second; `Counter`, `valkey.go`, `AllowWhileCounterDown` and the 10× multiple
  go · proved by `ratelimit_test.go` (rewritten), `fallback_test.go` folded in.
- [x] **T25** — `internal/otlp/gates.go`, `handler.go`, `traffic.go` · the limiter-down branch,
  `reasonLimiterDown`, `messageThrottledInProcess` go · `limiterfallback_test.go` deleted;
  `handler_test.go`, `grpc_test.go` lose their counter fakes.
- [x] **T26** — `cmd/receiver/main.go`, `internal/config/config.go` · Valkey dial, readiness check,
  `REDIS_URL` go; `RECEIVER_INSTANCES` (default 1) · proved by `config_test.go`.

### Step 5 — Redis is gone

*Judgeable:* `docker compose ps` shows no `redis`; `git grep -i -E 'celery|valkey|redis_url|probe_broker'`
outside `docs/` and the customer-peer files returns nothing; `just check` green.

- [x] **T27** — `compose.infra.yaml`, `docker-compose.yaml`, `.app.yaml`, `.onprem.yaml`, both
  `.env.example`, justfile comments, `python-dev.Dockerfile` comment · Redis service, volume, port, env and
  `depends_on` removed · proved by `test_compose_dependencies.py` (its `redis` exceptions go).
- [x] **T28** — `settings.py` (`redis_url`, `probe_broker_url`), `health/checks.py`, `lifespan.py`,
  scheduler `settings.py` (`redis_url`, `beat_leader_*` TTL) · proved by `test_settings.py`,
  `test_scheduler_settings.py`, `test_system.py`.
- [x] **T29** — `apps/api/pyproject.toml`, `apps/scheduler/pyproject.toml`, root `pyproject.toml` mypy
  overrides, `uv.lock`, `apps/receiver/go.mod`/`go.sum` · `redis`, `celery[redis]`, `valkey-go` removed.
- [x] **T30** — test environment: `environment.py:23`, `test_billing.py`, `test_notifications_console.py`,
  `test_system.py`, `test_cross_service_contracts.py:322,367` · `REDIS_URL` removed; T11 deleted.
- [x] **T31** — `docs/decisions/ADR-011…`, `ADR-020…`, `decision-log.md`, `docs/architecture/` pages that
  name Redis or Celery, `docs/operations/production-readiness.md` (group 2b done), `docs/changes/README.md`.

## New risks

- **Fork + asyncio.** Children must build their engine after fork, as Celery's do; `process_engine.py`
  already enforces this, and T18's tests run real children.
- **`SIGALRM` soft limit** interrupts only Python code; a task blocked in C (a socket read without a
  timeout) reaches the hard kill instead. Celery's soft limit had the same property.
- **One API process today.** `API_INSTANCES` defaults to 1, which is exact; the divided limits become
  approximate only when someone runs more and sets it. Group 6 (deployment config) owns making that value
  come from the deployment.
- **Up to a minute of API-token usage counts** is lost on a crash (T16). Shutdown flushes.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | `jobs` gets a nullable `org_id` so erasing an organization removes its queued work (sweeps carry none). Assumed yes. | No | `design.md` §1, amended at T1 |
| 2 | The queue's contract lives in py-platform as SQL text, its runtime in the scheduler (correction 1). Assumed yes. | No | `design.md` §3, amended at T17 |

## Gates

- [x] Specification approval — not applicable (no customer-facing change)
- [x] Design approval — 2026-10-10
- [x] **Plan approval — before the first edited file** — 2026-10-10
- [x] Implementation
- [x] Tests
- [x] Review
- [x] Design QA — not applicable
- [ ] Production-readiness assessment
- [x] Documentation and status update

## Validation plan

Built whole, then verified once: every new test seen red before green (recorded in `status.md`); `just
check` with counts read; the live suites above against the dev stack after Redis is removed; the step-3
judgeable checks by hand (worker kill, beat failover, Run now, send test).

## Rollout and rollback

Pre-deployment. Dev sessions sign out once at the switch. Rollback is `git revert` plus migration
downgrade; nothing in Redis is worth carrying back. The real risk is step 3: the queue replaces the thing
every check and alert runs on, so it is judged by hand before step 4 starts.

## Outcome

Built as planned in five steps; Redis, Celery and Valkey are gone from every service, compose file,
setting, dependency and test. `just check` green (Python 4209 passed, 3 pre-existing deliberate
skips; web 1352; browser SDK 120; Go and Python lint and types clean). Evidence per step is in
`status.md`.

What changed from the plan, and why:

- **Ten tables, not eight.** Second-factor state is three tables (a challenge is per sign-in, so
  keying it by user would let two browsers overwrite each other; the spent marker is keyed on the code,
  as in Redis), and view-as tickets are a typed table of their own.
- **`SecondFactorStore` commits on its own**, not on the request's transaction: a wrong code answers
  401, which rolls the request back, and the spent attempt must survive it.
- **T11 was done by hand** rather than as a test to be deleted in step 5.
- **The rate-limit callers suite became offline** (`test_ratelimit_callers.py`): its tiers are in
  memory now.
- **The API's Redis went in step 3**, not 5, once nothing in it used Redis.
- **A dead worker re-runs a job only if the job has attempts left** (record_outcome 6, pollers 3,
  everything else 1, covered by the next interval or sweep). Celery redelivered everything; this is
  bounded on purpose and a lost job is logged by name.
- **Session lookups by token hash go through three named helpers**, exempted from the tenancy check
  for `find_by_hash`'s reason; listing and ending one's own sessions now also filter by organization.
- **Found in the full check: the claim over-leased.** `id IN (SELECT … LIMIT n FOR UPDATE SKIP
  LOCKED)` let Postgres re-run the subquery; one claim for two slots took twenty jobs and its worker
  died holding them. The claim is now a `MATERIALIZED` CTE, and each worker's lease name is unique
  per worker rather than per process.
- **Not done by hand: a live ingest flood** for step 4. The 429-before-reading-the-body path is
  covered by the door tests over both transports.

