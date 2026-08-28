# 38. Per-tenant retention via conditional TTL

- **Status:** Superseded by [0072](0072-retention-class-partitioning.md)
- **Date:** 2026-08-26

## Context

Open since 2026-08 ([0017](0017-nine-architecture-additions-from-feature-audit.md), item 9):
ClickHouse TTL is table-level, but retention is a plan feature that differs per tenant. The options
looked like scheduled deletes or a partitioning change.

## Decision

Use ClickHouse's conditional TTL —
`ALTER TABLE ... MODIFY TTL toDateTime(ts) + INTERVAL 30 DAY WHERE tenant_id IN (...)` — with
`ttl_only_drop_parts=1` so whole parts are dropped rather than mutations run. A job regenerates the
TTL expression from plan tiers in Postgres.

Partitioning *by* `tenant_id` is rejected: it would explode part counts across many small tenants.
Daily date partitions stay.

## Consequences

> **Superseded.** The claim below was never tested and is wrong. Measured on 26.3, a conditional TTL
> makes ClickHouse **rewrite** the part — `ttl_only_drop_parts` cannot drop a part whose rows have
> not all expired, which is every part when tenants share one. See
> [0072](0072-retention-class-partitioning.md) for the test and the replacement.

No scheduled delete job, and no mutation load — expiry stays a cheap part drop. The cost is a
generated DDL expression that grows with the tenant list and has to be regenerated whenever a plan
changes.
