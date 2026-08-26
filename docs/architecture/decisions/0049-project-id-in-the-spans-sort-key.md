# 0049 — project_id in the spans sort key

- **Status:** Accepted
- **Date:** 2026-08-26
- **Supersedes:** [0018](0018-data-layer-design.md) in part — the `spans` ORDER BY

## Context

The `spans` schema in [0018](0018-data-layer-design.md) sorts by `(tenant_id, service_name, toStartOfHour(timestamp), trace_id)`. It was written before the tenancy model existed.

[0042](0042-tenancy-model-organization-and-project.md) then established that a **project is an environment** — production, staging — which means every query in the product filters by `project_id`, not merely most of them.

Without it in the sort key, staging and production rows interleave within the same granules, so every production read pulls staging data off disk to discard it.

## Decision

`ORDER BY (tenant_id, project_id, service_name, toStartOfHour(timestamp), trace_id)`.

Applied before any data existed, which is the only moment it is free.

## Consequences

- Production queries stop reading staging granules.
- Sort-key changes require rewriting the table, so doing this after ingestion begins would have meant a migration over the largest table in the system.
- `logs` and `metrics` keep their recorded sort keys for now; the same argument applies to them and is worth revisiting when their query patterns are real rather than predicted.
