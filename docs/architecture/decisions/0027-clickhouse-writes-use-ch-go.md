# 27. ClickHouse write path uses `ch-go`, not `clickhouse-go/v2`

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Our workers do one thing against ClickHouse: batched inserts. ClickHouse's own client documentation
recommends the low-level `ch-go` for insert-heavy workloads, because `clickhouse-go` adds a
row→column pivot plus reflection on every insert.

## Decision

Go workers write through `ch-go`. The Python read path continues to use `clickhouse-connect`.

Note this is a choice of layer, not of project: `clickhouse-go` v2.3+ wraps `ch-go` internally.

## Consequences

The insert path avoids per-row reflection at the point where volume is highest. Cost: `ch-go` is
column-oriented, so worker code builds columns rather than handing over structs — more code, and
schema changes touch it directly.
