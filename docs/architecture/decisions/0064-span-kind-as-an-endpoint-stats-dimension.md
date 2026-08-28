# 0064 — `span_kind` as a dimension of `endpoint_stats_1m`, not just a filter

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

The materialized view feeding `endpoint_stats_1m`
([000009](../../../deploy/clickhouse/migrations/000009_endpoint_stats_1m_mv.up.sql)) had no
`span_kind` predicate, and the target table had no `span_kind` column. Every span was rolled up as a
request served — including CLIENT spans, which are calls the service *made*, and INTERNAL spans,
which are not requests at all.

Measured on the dev fleet over one window: 44 SERVER spans against 33 INTERNAL and 18 CLIENT. The
rollup would have reported 95 requests for 44 real ones. P1-40's request rate inflated by more than
half, its error rate diluted by the same factor, and "slowest endpoints" listing outbound HTTP calls
as endpoints.

`span_kind` was already on `spans`, so nothing about this needed the pipeline to change.

The choice was between filtering the view to entry points and carrying `span_kind` as a dimension.

## Decision

Both, because they answer different questions.

The view filters to `span_kind IN ('SERVER', 'CONSUMER')`. CONSUMER is included because a queue
handler is an entry point in the same sense a SERVER span is — inbound work with a latency and an
outcome — and excluding it would quietly make asynchronous services look idle.

The table carries `span_kind` as a column, placed before `span_name` in the sort key as the
lower-cardinality of the two, so "every SERVER row for this service" is one contiguous range.

The asymmetry is the point. **The filter is reversible and the column is not.** Widening the view to
CLIENT spans later, to build dependency rollups on the same rollup, is one `CREATE OR REPLACE VIEW`.
Adding the column later means rebuilding a 13-month table.

## Consequences

The numbers on the dashboard mean what their names say, and a reader that forgets to filter cannot
reintroduce the bug — there is nothing wrong in the table to read.

Two kinds are no longer summed together: a service's HTTP endpoints and its queue handlers are
distinguishable rather than conflated into one request rate.

The rollup is **not** backfilled from `spans`, unlike the trace-id lookup in
[0063](0063-daily-partitions-on-the-trace-id-lookup.md). These counters merge by summing, so a minute
counted by both a live view and a backfill is counted twice, and there is no ordering of the two that
makes the overlap harmless the way min/max does. The rollup refills itself from the next minute
onward; the existing rows were wrong anyway. Backfilling a summing rollup on a live system needs a
window with the view stopped, which is an operational procedure rather than a migration.
