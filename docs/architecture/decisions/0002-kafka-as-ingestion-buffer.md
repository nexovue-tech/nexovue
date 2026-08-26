# 2. Kafka as the buffer between ingestion and processing

- **Status:** Superseded by [0020](0020-redpanda-replaces-kafka.md) — which was itself reversed by [0026](0026-apache-kafka-4-in-kraft-mode.md), so Kafka stands
- **Date:** 2026-08

## Context

The receiver accepts at whatever rate customers send; processing and ClickHouse writes proceed at
their own. Coupling them directly makes a traffic spike into backpressure that reaches storage, and
makes any processing bug an unrecoverable data loss.

## Decision

Put Kafka between ingestion and processing. Topics `spans-raw`, `logs-raw`, `metrics-raw`.

## Consequences

Spikes are absorbed, stages are decoupled and scale independently, and the stream is replayable —
a processing bug can be fixed and the data reprocessed. Cost: a broker to operate, and disk sizing
for it on a single host.
