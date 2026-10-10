# Change: redis-goes — readiness group 2b (ADR-020)

## Classification

Infrastructure and security.

## Current behavior and problem

Blocker 15 (`docs/operations/production-readiness.md`): one Valkey runs `allkeys-lru` at 384 MB yet holds
state that cannot be rebuilt — sessions, reset and verification links, the Celery broker, the beat
leader lock. ADR-020 (2026-10-10) decided Redis goes: what it holds moves to Postgres, and Celery is
replaced.

Also carried from 2a: an organization-wide in-flight ceiling for API tokens, which belongs with the
rate-limit redesign here.

## Research (2026-10-10, before design)

### What we use today, read from the code

- **Celery** (`apps/scheduler/src/nexovue_scheduler/celery_app.py:40-107`): Redis broker, no results
  backend; queues `light`, `light.<location>` per probe location, `deliveries`; per-dispatch soft and hard
  time limits, `task_acks_late`, visibility timeout twice the hard limit. Tasks: `RUN_CHECK`,
  `RECORD_OUTCOME` (retries with `countdown=2**n`), heartbeat and file sweeps (`probes/tasks.py`);
  `DELIVER`, `SWEEP_DELIVERIES`, `SEND_TEST`, `PRUNE_ATTEMPTS` (`alerts/tasks.py`); three evaluation
  sweeps (`alerts/sweeps.py`); CloudWatch and Postgres pollers (`pollers/tasks.py`). Features relied on:
  `send_task` with `countdown` and `expires`, per-dispatch time limits, retries with delay, `acks_late`.
  Not used: chords, groups, results, rate limits, revocation.
- **Beat** (`beat.py`): a schedule rebuilt from the `checks` and integrations tables on a timer, with a
  per-check offset; fixed sweeps; only the leader schedules, by a Redis lock (`leader.py`).
- **API dispatch**: Run now and channel send-tests via its own Celery client (`checks/run_now.py`,
  ADR-011).
- **Other Redis users**: sessions with a per-user index (`auth/sessions.py`); links with atomic `GETDEL`
  (`auth/tokens.py`); TOTP replay (`auth/totp.py`); support tickets (`support/tickets.py`); API-token usage
  counters (`apitokens/usage.py`); alert evaluation state with Lua compare-and-set (`alerts/evaluation.py`,
  `alerts/sweeps.py`); API rate limits, a sorted-set sliding window plus in-flight caps
  (`ratelimit/limiter.py`); the receiver's limiter (`valkey.go`, already falls back in-process).

### Candidate queues

| | procrastinate | PgQueuer | Hand-rolled on `SKIP LOCKED` |
|---|---|---|---|
| Health | 3.10.0 (2026-09-23), MIT, 3 maintainers seeking more | v1.5 era (Oct 2026), MIT, one active maintainer | our pattern already runs `alerts/outbox.py` and probe claims |
| Sync/async | both, psycopg only | asyncio only, asyncpg | either |
| Our table-driven schedule | no — cron is static; our beat stays | no — same | yes |
| Dead worker | heartbeat, but re-queueing stalled jobs is manual | heartbeat re-queues | lease column, as the outbox does |
| Per-task hard time limit | not documented | not documented | build a supervised child process |

Sources: procrastinate on PyPI and its docs (cron, retry stalled jobs); PgQueuer on PyPI, its docs and
releases (PR #917); comparisons from Judoscale and Hacker News on Chancy and Hatchet.

**Recommendation from the research:** hand-rolled. Neither library removes the two hard parts — our
own scheduler (the schedule is a table, not a cron) and per-task hard time limits — and each adds a
gap of its own. Estimated about 500 lines plus tests.

### Rate limits without Redis

Grafana Mimir enforces tenant limits per distributor at limit ÷ healthy replicas. Postgres counters
measured elsewhere at roughly 6.7k-15k requests a second per node before contention; fixed windows cost
one upsert per key per window. Proposed: read tiers and in-flight caps in-process at limit ÷ N; the
auth tier as a Postgres fixed-window counter; the receiver on its existing in-process limiter.

### Each Redis job, checked against Postgres (2026-10-10)

| Redis job | Postgres way | Verdict |
|---|---|---|
| Sessions | a **logged** table with `expires_at`, swept on a schedule | Works. **Not `UNLOGGED`**: an unlogged table is truncated after a crash and not replicated, so a failover would sign everyone out |
| One-time links, support tickets (`GETDEL`) | `DELETE … RETURNING` | Works, atomically |
| TOTP replay | a unique `(user_id, step)` insert | Works |
| Expiry (TTL) | no native TTL; an `expires_at` column, reads filtered on it, a sweep job | Works; one more scheduled job |
| API-token usage counters | counted in process, flushed by upsert | Works |
| Alert evaluation state (Lua compare-and-set) | conditional `UPDATE … WHERE` / upsert | Works |
| Rate limits, auth tier | `INSERT … ON CONFLICT DO UPDATE` fixed window | Works; milliseconds, not sub-millisecond; volume is low |
| Rate limits, read tiers | in process, limit ÷ containers (as Grafana Mimir does) | Works; per-request Postgres writes would be the wrong cost |
| Receiver limiter | in process (already its fallback) | Done |
| Job queue | `FOR UPDATE SKIP LOCKED` | Works; already ours twice |
| Beat leader | advisory lock — released when the holder's connection dies, no TTL to get wrong | Works; breaks under a transaction-mode pooler (PgBouncer), which the stack does not use |
| Run now wake-up | `LISTEN/NOTIFY` | Works at our volume. NOTIFY takes a cluster-wide lock at commit — the cause of Recall.ai's 2025 outages under thousands of notifying writes a second; DBOS reached 60k/s by batching. Confine it to Run now |

Sources: Raphael De Lio, "Can Postgres replace Redis as a cache?"; dev.to "I replaced Redis with
PostgreSQL"; Tihomir Manushev on Medium; Recall.ai, "Postgres LISTEN/NOTIFY does not scale"; DBOS,
"Postgres LISTEN/NOTIFY actually scales"; pg-ratelimit (fixed window); Neon's rate-limiting guide;
dev.to "You probably don't need Redis for distributed locking".

This settles decisions 3 and 4 below as recommendations: a logged sessions table read on every request
(revocation immediate), and NOTIFY for Run now only.

## Decisions taken (founder, 2026-10-10)

- **Redis is removed entirely — not kept as an option** for production or anywhere else (ADR-020
  unchanged). Raised and rejected: Redis optional for rate-limit counters only. The founder chose one
  way to run, in every deployment.
- **Rate limits (decision 2 below): read tiers and in-flight caps in process, each API container taking
  its share; the auth tier (login, signup, reset) as an exact Postgres fixed-window counter.** Accepted
  consequence: read limits are approximate across several API containers when traffic is uneven; they
  protect ClickHouse from load, not anything an attacker wants, and the limits that do stay exact.
- **Decision 1: a hand-rolled Postgres queue on `FOR UPDATE SKIP LOCKED`** — not procrastinate.
- Decisions 3 and 4 below taken as the research recommends: a logged sessions table read on every
  request; NOTIFY for Run now only.

## Open decisions for the founder, before design

1. Hand-rolled queue (recommended) or procrastinate?
2. In-process limit ÷ N for read limits and in-flight caps, with Postgres counters only for login,
   signup and reset?
3. Sessions in Postgres: one indexed read per authenticated request, or a few-second in-process cache
   that delays a revocation by that long?
4. Run now: sub-second via LISTEN/NOTIFY, or a one-second poll?

## Status

Proposed — research done; design waits on the four decisions above and on group 2a.
