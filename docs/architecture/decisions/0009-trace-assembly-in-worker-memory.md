# 9. Trace assembly = in-worker memory plus Kafka partitioning by trace_id

- **Status:** Accepted
- **Date:** 2026-08

## Context

A trace arrives as spans scattered across time and services. Joining them needs either shared state
(Redis, a database) or a guarantee that all spans of one trace reach the same process.

## Decision

Partition Kafka by `trace_id`. That guarantees every span of a trace lands on the same worker, so
assembly is a plain in-memory dict with no shared state. Wait window roughly 30s; spans arriving
after it are written individually and joined at query time.

## Consequences

No shared store on the hot path and no distributed coordination. In exchange, the partitioning key
becomes load-bearing — it cannot be changed without redesigning assembly — and a worker restart
loses its in-flight buffer, which the query-time join covers. Confirmed against OTel guidance in
[0039](0039-confirmed-go-workers-and-own-tail-sampling.md).
