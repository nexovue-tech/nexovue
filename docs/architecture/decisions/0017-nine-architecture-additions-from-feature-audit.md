# 17. Full-feature audit — core architecture unchanged, nine additions adopted

- **Status:** Accepted
- **Date:** 2026-08

## Context

The architecture had been designed against a partial feature set. Before committing to it, the full
market feature list was audited against it to find out what would break.

## Decision

The core — doors → receiver/Celery → Kafka → Go workers → ClickHouse/Postgres — holds for the full
feature list. Nine additions are adopted (detail in `../system-architecture.md` §5d):

1. **Service map** — dependency edges derived in Go workers from parent-child spans crossing
   service boundaries, into a small ClickHouse table. No new collection.
2. **SLO/SLI tracking** — definitions in Postgres, computed from the existing `endpoint_stats_1m`.
3. **Live tail** — a real-time path: backend WebSocket over the Kafka logs topic or Redis pub/sub,
   tenant-filtered. ClickHouse cannot serve this because inserts are batched.
4. **Usage metering** — per-tenant ingest counters extending the receiver's rate-limit counters.
5. **PII masking** — a masking step in workers, rules configurable in Postgres.
6. **Log pattern grouping** — Drain-style templating attaching a `pattern_id` to logs.
7. **Continuous profiling** — OTLP-profiles in the receiver plus a flame-graph table.
8. **Ad-hoc query layer** — one flexible layer over ClickHouse serving dashboards, chatbot
   tool-calling and outlier analysis. Designed once.
9. **RBAC and per-tenant retention** — roles and permissions in Postgres. Per-tenant retention was
   left an open refinement, since ClickHouse TTL is table-level.

## Consequences

The architecture is validated against the whole roadmap rather than the next milestone, so none of
these nine arrive as surprises. Two of them add genuinely new paths — the real-time WebSocket path
and the profiles table — that the batch pipeline does not cover. Item 9's open half was resolved in
[0038](0038-per-tenant-retention-via-conditional-ttl.md).
