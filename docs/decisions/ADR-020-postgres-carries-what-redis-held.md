# ADR-020: Postgres carries what Redis held, Redis goes, and object storage is optional

## Status

Accepted 2026-10-10 — explicit founder decision, reached while planning the Phase 1 Track 3 fixes.
Not yet executed. Redis's removal is built in group 2 of the fix order in
`docs/operations/production-readiness.md`; the local-disk object store with the self-hosted install
(group 8, F37). Postgres stays **required** — considered and kept, see *Rejected*.

## Context

A self-hosted install needs five infrastructure services today: ClickHouse, Kafka, Postgres, Redis
(Valkey) and S3-compatible object storage (SeaweedFS). The founder asked how many could become
optional, so that a developer can pull nexovue and run it with as little as possible.

ClickHouse and Kafka are what nexovue is — the telemetry store and the durable buffer between intake
and storage (ADR-001, ADR-002) — and were not in question.

**Redis holds, as of 2026-10-10:** login sessions (`auth/sessions.py`), password-reset, verification
and email-change links (`auth/tokens.py`), TOTP replay state (`auth/totp.py`), support view-as tickets
(`support/tickets.py`), API-token usage counters folded daily into Postgres (`apitokens/usage.py`),
alert evaluation state the scheduler leaves for the API (`alerts/evaluation.py`), the API's rate-limit
counters (`ratelimit/limiter.py`), the receiver's rate-limit counters (`internal/ratelimit/valkey.go`,
which already falls back to an in-process bound), the Celery broker for every scheduler queue and for
Run now (`celery_app.py`, `checks/run_now.py`), and the beat leader lock (`leader.py`).

Production-readiness blocker 15 found that this instance runs `allkeys-lru` at 384 MB, so under memory
pressure it silently evicts sessions, reset links, queued check runs and the leader lock.

**Object storage holds** RUM source maps, files attached to synthetic checks, and the objects removed
when an organization is erased. The API already treats it as optional (`deps.get_object_store` returns
`None` when none is configured), but then those features stop working.

## Decision

1. **Redis is removed, not made optional.** What it holds moves to Postgres:

   | Today in Redis | Moves to |
   |---|---|
   | Sessions, reset/verify/email-change links, TOTP replay state, support tickets | Postgres tables with expiry, swept by the scheduler |
   | API-token usage counters, alert evaluation state | Postgres rows |
   | Beat leader lock | A Postgres advisory lock |
   | Celery broker (scheduler queues, Run now) | A Postgres-backed task queue — **Celery is replaced**; which queue is a group 2 design question, researched then |
   | API and receiver rate-limit counters | Counted in process, as the receiver's fallback already does |

   One service fewer on every install, and blocker 15 disappears rather than being patched.

2. **Object storage becomes optional through a second backend:** a local directory on a volume, for a
   single-server install. S3-compatible storage remains the backend whenever more than one server
   runs the API or scheduler, since a local directory is not shared between hosts.

3. **Postgres stays required**, and an install never asks anyone to configure it: the installer
   generates its password and the deploy step creates its schema (F37).

The infrastructure an install needs becomes **ClickHouse, Kafka and Postgres**, plus local disk.

## Open, for group 2's design

- **The Postgres task queue.** Celery's documentation lists only RabbitMQ, Redis and SQS as stable
  brokers; its SQLAlchemy transport is experimental and can run messages twice with several workers.
  Candidates (procrastinate, PgQueuer, or our own on the `SKIP LOCKED` pattern the alert outbox and
  probe claims already use) are compared there, against current maintenance and our own load.
- **Rate limits across many API containers.** In-process counters multiply every limit by the number
  of API containers. Acceptable for one server; whether the hosted cloud keeps a shared counter — and
  where — is decided in that design, not assumed here.

## Rejected

- **Make Redis optional.** Two implementations of sessions, the queue and the lock, each tested on
  every change for good — the cost of "optional" without its benefit, since Postgres is present anyway.
- **Keep Redis and split it** (the original group 2 fix for blocker 15). Fixes the eviction, keeps a
  fifth service on every install.
- **SQLite instead of Postgres by default**, as SigNoz does. SQLite allows one writer at a time, and
  nexovue has six writers across two languages — the API, three scheduler services, the receiver and
  the worker; the alert outbox and probe claims depend on `FOR UPDATE SKIP LOCKED`, which SQLite does
  not have; and the hosted cloud needs Postgres regardless, so every migration and query would exist
  twice. SigNoz's own team wrote about outgrowing SQLite for its single-writer limit.

## Consequences

- **Postgres takes more load**: every authenticated request reads a session row instead of a Redis
  key. Sessions need an index on their token hash and expiry, and the sweep that removes expired rows
  is a scheduler job.
- **The scheduler's queue changes underneath every task.** Probe runs, alert sweeps, deliveries and
  Run now all move together; their time limits and acknowledgement guarantees (`celery_app.py`) are
  restated against whichever queue is chosen.
- **ADR-011** (the API dispatches Run now to the scheduler's broker) and **ADR-013** (notifications are
  an outbox) are amended by this when group 2 is built: the broker becomes Postgres.
- `compose.infra.yaml`, both deployment files and the backup tooling lose Redis; the backup format
  loses nothing, because nothing in Redis was ever durable.
