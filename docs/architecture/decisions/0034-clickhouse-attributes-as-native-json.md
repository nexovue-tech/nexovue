# 34. ClickHouse attributes use the native `JSON` type, not `Map`

- **Status:** Accepted
- **Supersedes in part:** [0018](0018-data-layer-design.md) — principle 2's `Map(LowCardinality(String), String)` attribute column
- **Date:** 2026-08-25

## Context

[0018](0018-data-layer-design.md) stored flexible attributes as
`Map(LowCardinality(String), String)`. ClickStack — ClickHouse's own OpenTelemetry stack — migrated
off Map to the native `JSON` type and reports roughly 9× faster queries.

The reason is structural. Map keys are not true subcolumns: reading *any* key loads the entire map
into memory, and keys cannot be individually indexed or compressed. The `JSON` type stores each
path as a real subcolumn.

## Decision

Attributes use ClickHouse's native `JSON` type. Pin a ClickHouse version with production JSON
support in compose.

## Consequences

A query touching one attribute no longer reads the rest. This is the exact access pattern the
chatbot's tool-calling and BubbleUp-style outlier analysis depend on, so the difference compounds
rather than being a one-off win. Cost: a minimum ClickHouse version is now a hard requirement of
the schema, pinned in compose.
