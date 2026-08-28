# 0063 — Daily partitions on the trace-id lookup table

- **Status:** Accepted
- **Date:** 2026-08-28


> **Migration numbers in this record predate the 2026-08-28 squash.** The migrations named
> here no longer exist as separate files; each table's final shape is now one file, and the
> links point there. See [0073](0073-migrations-squashed-before-m3.md).

## Context

`span_trace_lookup` (its original migration, now [000009_span_trace_lookup](../../../deploy/clickhouse/migrations/000009_span_trace_lookup.up.sql))
shipped with a 30-day TTL and no `PARTITION BY`. Those two facts do not coexist.

[0038](0038-per-tenant-retention-via-conditional-ttl.md) set `ttl_only_drop_parts = 1`, which is what
makes expiry a cheap part drop rather than a mutation. A part is dropped only when its **newest** row
has expired. With one partition, merges combine parts that span the whole retention window, so every
merged part contains rows from today — and nothing is ever dropped. The table's TTL was decorative.

It was already visible: 2.6M rows and 113 MiB, growing without bound, while the dev retention script
trimmed `spans` beside it on schedule. Nobody noticed because the table is small per row.

The OpenTelemetry ClickHouse exporter ships the equivalent table. Its
`internal/sqltemplates/traces_id_ts_lookup_table.sql` reads
`PARTITION BY toDate(Start)`, `ORDER BY (TraceId, Start)`,
`SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1` — daily partitions on the start
timestamp, and the same `ttl_only_drop_parts` we already set globally.

## Decision

`PARTITION BY toDate(span_start)`. Retention stays at the 30 days the table already declared, which
is `spans`' window; the sort key stays `(tenant_id, trace_id)`, which is how the API reads it.

The TTL continues to key off `span_end` while the partition keys off `span_start`. A trace's two
timestamps are seconds apart, so a day's partition expires within a day of nominal — correct, and it
keeps the TTL meaning "thirty days after the trace finished".

A partition key cannot be altered, so this is a rebuild: drop the materialized view, replace the
table, recreate the view, then refill from `spans`.

The refill runs **after** the view is back, not before. Readers aggregate `min(span_start)` and
`max(span_end)`, so a trace written by both the view and the backfill yields the same window —
overlap is harmless where a gap would not be. That ordering is specific to this table and would be
wrong for a summing rollup; see [0064](0064-span-kind-as-an-endpoint-stats-dimension.md).

## Consequences

The TTL works. The table stops growing without bound, and expiry stays a part drop.

Opening a trace by id with no time bound now reads 30 partition indexes instead of one. That is the
standard cost of partitioning and the exporter accepts it too; the read is bounded by the primary
index in each, and the table exists precisely so the *next* query has a time bound.

This was the hardest one-way door in the review. Doing it now cost one rebuild of 2.6M rows in
1.5 seconds. The same rebuild against a populated production table is a maintenance window, because
the refill is one group per trace over the whole retention window — an unbounded aggregation that
exhausted a 2.5 GiB dev server outright before the spill threshold was set on it.
