# 39. Confirmed by research: Go workers and our own tail sampling both stand

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

Two shortcuts were worth ruling out before building: letting ClickHouse's Kafka table engine
replace the Go workers, and letting the OTel collector's `tailsampling` processor replace our own.

## Decision

Both stand as designed.

ClickHouse's Kafka table engine only moves messages into a table — trace assembly, tail sampling,
masking and metric derivation have nowhere to run inside it.

The collector's `tailsampling` processor cannot scale without a two-layer collector deployment, and
the OTel docs name our exact design as the alternative: route spans through Kafka partitioned by
trace ID ([0009](0009-trace-assembly-in-worker-memory.md)).

## Consequences

The two largest pieces of custom data-plane code are confirmed as necessary rather than
self-inflicted, and the reasons are on record for the next time someone asks why we did not just
use the collector.
