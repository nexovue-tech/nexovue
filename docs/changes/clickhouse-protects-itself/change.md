# Change: clickhouse-protects-itself — readiness group 2a

## Classification

Security and infrastructure.

## Current behavior and problem

From `docs/operations/production-readiness.md` (2026-10-10):

- **Blocker 14 — no read is bounded in memory.** `READ_SETTINGS` caps time and rows only
  (`packages/py-platform/.../tenant_statements.py:36-40`); the server reports `max_memory_usage = 0`.
  One heavy read can take the shared ClickHouse to its server ceiling, where every query fails — the
  workers' inserts included.
- **One all-powerful ClickHouse account.** The API, the scheduler and the workers all connect as the
  account the server was created with: every grant, including DROP, FILE, URL and S3. Tenant separation
  rests on the code-level check alone, and nothing outside the code bounds a read.
- **The live tail is outside every limit.** WebSockets skip the rate limiter, and `logs/tail.py` starts
  a poller per `project_id` it is handed without checking it — each one a ClickHouse query a second.
- **The LLM-calls read has no row limit** (`llm_observability/queries.py`) — found in F29, the work just
  shipped.
- **Blocker 23 — the hosted scheduler's memory limit is one dev saw kill it.** `scheduler-light` at
  256M in `docker-compose.app.yaml`; dev runs 768M after measuring the OOM.

Moved to group 2b: the organization-wide ceiling for API tokens. The rate limiter cannot see a token's
organization without a database read, and 2b redesigns rate limiting when Redis goes (ADR-020).

## Intended behavior

Every read is bounded in memory twice — by the settings the code sends and by a ceiling the server
enforces whatever the code sends. Readers connect as a user that can only read. A tail can only be
opened on the caller's own project, and a person can hold only a few. The LLM-calls read is capped and
says so. The hosted scheduler has the memory dev measured it needs.

## Affected specifications and architecture

None changed. ADR-012 (the scheduler reads ClickHouse through shared, tenant-bound statements) holds.

## Risks and rollback

In `plan.md`.

## Plan and validation

[`plan.md`](plan.md).

## Status

[`status.md`](status.md).
