# Design: Redis goes (readiness group 2b)

## Status

Approved by the founder, 2026-10-10

## Context and goal

ADR-020 (2026-10-10) decided Redis goes: what it holds moves to Postgres and Celery is replaced. The
founder's decisions since (`change.md`): **no Redis anywhere, not even optional**; a **hand-rolled
Postgres queue**; read limits counted **in each API process**, the auth tier counted **exactly in
Postgres**; sessions in a **logged** table read on every request; **NOTIFY for Run now only**.

The goal: nexovue runs on ClickHouse, Kafka and Postgres alone, and nothing it must not lose lives in a
store that evicts.

The inventory this rests on, with file and line for every use, is in `change.md` and the agent report
it records. In one table:

| Today in Redis | Commands relied on | Moves to |
|---|---|---|
| Sessions + per-user index (`auth/sessions.py`) | SET ex, GET, SET keepttl, ZADD/ZRANGE/ZREM, MGET, DEL | `sessions` table |
| One-time links (`auth/tokens.py`), view-as tickets (`support/tickets.py`) | SET ex, GETDEL | `one_time_tokens` table |
| TOTP enrolment, challenge (5 attempts), spent step (`auth/totp.py`) | SET ex, GET, DEL, SET nx ex | `second_factor_state`, `totp_spent_steps` |
| API-token usage counters (`apitokens/usage.py`) | INCR, EXPIRE, SET nx ex; folded to Postgres each minute | counted in process, folded the same way |
| Alert evaluation status (`alerts/sweeps.py` → `alerts/evaluation.py`) | SET ex 3600, GET | `alert_evaluation_status` table |
| Run-now and send-test results (`checks/run_now.py`, `channels/send_test.py`) | SET ex 300, GET | `job_results` table |
| API rate limits (`ratelimit/limiter.py`, 2 Lua scripts) | sorted-set sliding window; in-flight slots | in process; auth tier in Postgres |
| Receiver rate limit (`valkey.go`) | INCRBY + EXPIRE NX | in process (its existing fallback) |
| Celery broker: queues `light`, `light.<region>`, `deliveries` | Celery/kombu | `jobs` table |
| Beat leader (`leader.py`, 2 Lua scripts) | SET NX PX, refresh/release if ours | Postgres advisory lock |

## Proposed design

### 1. Tables (one Alembic migration), all logged

Not `UNLOGGED`: an unlogged table is truncated after a crash and not replicated, so a failover would
sign everyone out and drop queued work — the property ADR-020 removes Redis for.

| Table | Key | Columns | Expiry |
|---|---|---|---|
| `sessions` | `token_hash` (SHA-256, as the Redis key was) | `user_id`, `org_id`, `created_at`, `last_seen_at`, `expires_at`, `client`, `impersonation`, `admin_until` | `expires_at`, fixed at creation and capped by an impersonation's end, as today |
| `one_time_tokens` | `(purpose, fingerprint)` | `payload`, `expires_at` | as minted; purposes include `view-as` |
| `second_factor_state` | `(user_id, kind)` — kind `enrolment` or `challenge` | `payload`, `attempts`, `expires_at` | 600 s / 300 s |
| `totp_spent_steps` | `(user_id, step)` | `expires_at` | 90 s |
| `alert_evaluation_status` | the status key's parts | `status`, `recorded_at` | read as behind after 3 minutes, as today |
| `job_results` | `request_id` | `kind`, `payload`, `expires_at` | 300 s |
| `rate_limit_windows` | `(key, window_start)` | `count` | the window |
| `jobs` | `id` | see §3 | — |

Every Redis atomic has a single-statement equivalent:

- `GETDEL` (spend a link or ticket) → `DELETE … WHERE … AND expires_at > now() RETURNING payload`.
- `SET NX` (claim a TOTP step) → `INSERT … ON CONFLICT DO NOTHING RETURNING`.
- Challenge attempts → `UPDATE … SET attempts = attempts + 1 … RETURNING attempts`.
- The session index (`ZSET` per user) → an index on `sessions (user_id, expires_at)`; listing is one
  `SELECT`, revoking one is a `DELETE … WHERE token_hash = … AND user_id = …` (the membership check
  *is* the predicate, as `ZSCORE` was).

**Expiry is a sweep**, since Postgres has no TTL: every read filters `expires_at > now()`, and one
scheduled job deletes expired rows from all the tables above in batches (§3). A missed sweep costs disk,
never correctness.

### 2. Sessions

Read on every authenticated request by primary key, as decided — revocation takes effect on the next
request. `last_seen_at` is still rewritten at most once a minute per session (`auth/sessions.py:65`), so
reads stay reads. `users.sessions_valid_from` keeps doing what it does. `SessionStore` keeps its
interface; only its storage changes, so `auth/dependencies.py`, `profile/`, `superadmin/` and `support/`
are untouched.

The unused `extend` method (`auth/sessions.py:258`, no caller) is deleted.

### 3. The job queue (replacing Celery)

A small package shared by the API and the scheduler, `nexovue_platform.jobs`, on the
`FOR UPDATE SKIP LOCKED` pattern the alert outbox (`alerts/outbox.py`) and probe claims already run.

**`jobs` columns:** `id`, `queue`, `task`, `kwargs` (JSON), `run_after`, `expires_at` (nullable),
`attempts`, `max_attempts`, `soft_limit_s`, `hard_limit_s`, `lease_until` (nullable), `leased_by`,
`created_at`, `last_error`. A partial index on `(queue, run_after)` for unleased rows.

**Enqueue** — `INSERT` (with `run_after` for a countdown, `expires_at` for an expiry, per-job limits).
The API enqueues Run now and send-tests directly, then `NOTIFY jobs_<queue>` in the same transaction.
Nothing else notifies: beat's scheduled work is picked up by the next poll, so the cluster-wide commit
lock NOTIFY takes (Recall.ai, 2025) is paid only for an interactive click.

**Claim** — a worker `UPDATE jobs SET lease_until = now() + hard_limit + grace, leased_by = …,
attempts = attempts + 1 WHERE id IN (SELECT id … WHERE queue = ANY(…) AND run_after <= now() AND
(lease_until IS NULL OR lease_until < now()) ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT n)
RETURNING …`. It polls every second and wakes early on `LISTEN`. A job past `expires_at` is dropped
at claim — what Celery's `expires` did for a confirmation run (`probes/tasks.py:380-396`).

**Run** — each job runs in a **supervised child process**, as Celery's prefork pool ran it:
- the soft limit raises an exception inside the job (an alarm signal), so a task can still clean up as
  it does today;
- the hard limit kills the child, which is replaced; the job is then failed or retried by its
  `max_attempts`;
- a long-lived pool of N children per worker, N the existing concurrency (8 for `scheduler-light`, 4 for
  `scheduler-deliveries`).

**Finish** — success deletes the row. A task asking for a retry with a countdown (as `RECORD_OUTCOME`
and the pollers do, `probes/tasks.py:209`, `pollers/tasks.py:231`) sets `run_after` and clears the
lease. **A worker that dies mid-job** leaves its lease to expire, and the job is claimed again — the
same at-least-once guarantee as `acks_late` with a visibility timeout, and the lease is longer than the
hard limit, so a job is killed before it can be claimed twice.

**Queues and workers** — the same names (`light`, `light.<region>`, `deliveries`) and the same split:
`scheduler-light` claims everything but `deliveries`, `scheduler-deliveries` claims only `deliveries`.
Task names stay; `send_task` becomes `jobs.enqueue`, and the cross-service contract test
(`test_cross_service_contracts.py`) holds the API's enqueues against the worker's registry as it held
`send_task` against Celery.

**Results** — Run now and send-test write `job_results`; the API's existing 25-second wait reads it.

### 4. Beat and its leader

`beat.py` keeps building its table-driven schedule; it enqueues instead of `send_task`. Leadership is
`pg_try_advisory_lock` on a dedicated connection held for the leader's life: a leader that dies drops
its connection and Postgres releases the lock — no TTL to get wrong, which is where `leader.py`'s
refresh-and-release scripts came from. The standby beat retries every 5 s (`leader.py` settings), as
now. (A transaction-mode pooler such as PgBouncer would break a session lock; the stack runs none, and
the design records that as a constraint.)

### 5. Rate limits

- **Read tiers and in-flight caps** — counted **in process**: a sliding window per identity in a bounded
  map, and in-flight slots as process counters released on response, exactly where `release_slot` is
  today. Each API process applies `limit ÷ API_INSTANCES` (a setting, default 1) — Grafana Mimir's
  approach. Exact on one process; approximate across several when traffic is uneven, accepted by the
  founder.
- **The auth tier** (login, signup, reset, second factor — `ratelimit/policy.py:60`) — exact, in
  Postgres: one `INSERT … ON CONFLICT DO UPDATE` per attempt on `rate_limit_windows`, a fixed one-minute
  window. **Fails closed** if Postgres cannot answer — the opposite of the read tiers, and on purpose:
  this tier exists to stop password guessing.
- **API tokens gain an organization-wide ceiling** (moved here from 2a): a token's organization is
  resolved once and cached in process for 60 s, and the token tier gets an `org_max_concurrent` like the
  telemetry tier's 32.
- **The receiver** loses Valkey; its in-process limiter (`fallback.go`, today the fallback) becomes the
  limiter, at `RECEIVER_RATE_LIMIT_PER_SECOND ÷ RECEIVER_INSTANCES`.

### 6. What is deleted

Redis and Celery entirely: the `redis` service in all three compose files and `compose.infra.yaml`;
`REDIS_URL` and `PROBE_BROKER_URL` from every service's settings; `celery[redis]` and `redis` from both
Python projects and `valkey-go` from the receiver; `leader.py`'s scripts; `ratelimit/limiter.py`'s
scripts; the Redis health checks (API, receiver; the scheduler's has no caller today); the test fakes
that stand in for Redis. Run now and send-test stop being optional — they no longer need a separate
broker URL to exist.

## Data flow and interfaces

```
console ─► API ──INSERT jobs + NOTIFY──► Postgres ◄──claim SKIP LOCKED── scheduler workers ─► probes, deliveries
                 ◄── job_results ─────────────────────── write result ──┘
beat (advisory-lock leader) ──INSERT jobs (no NOTIFY)──► Postgres
every request ─► API ── sessions (PK read) ─► Postgres
login/signup/reset ─► rate_limit_windows (upsert) ─► Postgres
```

## Data and migration impact

One Alembic migration adds the tables above; its downgrade drops them. **Nothing migrates out of
Redis**: pre-deployment, so the one cost is that every development session signs out once and anything
queued at the switch is not carried over — the scheduler re-derives its schedule from Postgres anyway.

## Performance and scale

- **Sessions:** one primary-key read per request (sub-millisecond on a warm index) and at most one write
  per session a minute. At today's scale this is noise beside the ClickHouse reads a screen makes.
- **Jobs:** one claim query per poll per worker, plus inserts at beat's rate — per check per interval.
  The outbox already runs this pattern at the same order of volume.
- **Auth-tier counters:** one upsert per auth attempt; published measurements put a single Postgres node
  in the thousands a second, against an auth rate measured in single digits.
- **Table bloat** from churn (sessions touched, jobs deleted): autovacuum handles it at this volume; the
  sweep deletes in batches. Measured, not assumed, at the group 8 load test.

## Failure handling

| Failure | Behaviour |
|---|---|
| Postgres down | Everything that needs it stops — as today, where Postgres was already required by every request |
| Worker killed mid-job | Lease expires; another worker claims the job (attempts + 1) |
| Job runs past its hard limit | Child killed and replaced; job retried or failed by `max_attempts` |
| Beat leader dies | Connection drops, lock released; the standby takes over within 5 s |
| Expiry sweep misses a run | Rows linger; every read already filters `expires_at > now()` |
| NOTIFY lost | The job is claimed on the next one-second poll |

## Security, tenancy, and privacy

- Tokens are stored as SHA-256 hashes, as the Redis keys were; no credential is stored in the clear.
- Job `kwargs` carry what Celery messages carried — check secrets travel as names, never values
  (ADR-010) — now in Postgres, which is backed up and encrypted at rest where Redis was neither.
- Every new table carrying an organization is listed in the tenant-erasure inventory, which its live test
  (`test_deletion_live.py`) fails on if one is missed.

## Observability and operations

Queue depth and oldest-job age become SQL over `jobs` — what group 7 needs to report the scheduler's
health, and what Redis gave us nowhere. The heartbeat lines stay.

## Alternatives and tradeoffs

- **Keep Redis, split it** — rejected in ADR-020.
- **Redis optional for rate-limit counters** — raised and rejected by the founder (2026-10-10).
- **procrastinate** — rejected by the founder: our schedule is table-driven, so our beat stays; no
  documented hard time limit; stalled jobs re-queued by hand.
- **UNLOGGED tables** — rejected: a crash or failover empties them.
- **NOTIFY for every job** — rejected: the commit-time lock is paid per notifying commit; beat's volume
  gains nothing from sub-second pickup.

## Specification impact

None. Amends **ADR-011** (Run now dispatches through the `jobs` table and reads `job_results`; the API's
broker dependency goes) and closes **ADR-020**'s two open questions (the queue, and rate limits across
containers). **ADR-013** (the outbox) is unchanged; its `DELIVER` dispatch moves from Celery to `jobs`.
