# 3. ClickHouse for telemetry, Postgres for the control plane

- **Status:** Accepted
- **Date:** 2026-08

## Context

Two workloads with nothing in common. Telemetry is high-volume, append-only, and queried by scan
and aggregate. Users, projects, API keys, alert rules, cases and findings are low-volume relational
business data that must be transactionally correct.

## Decision

ClickHouse stores traces, logs and derived metrics — columnar, roughly 10x compression, fast scans,
hot window 15–30 days. Postgres stores the control plane. The split between the two planes is
strict: they meet in exactly two places — the gateway reading API keys to admit telemetry, and the
insight engine writing findings and cases after analysis.

## Consequences

Each side scales on its own terms and neither compromises for the other. Two databases to operate
instead of one, and any feature spanning both planes does the join in application code — there is
no cross-database foreign key.
