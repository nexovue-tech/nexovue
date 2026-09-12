# ADR-002: Acknowledge only durable boundaries

## Status

Accepted — existing implementation.

## Context

Telemetry loss is worse than duplicate delivery, but a distributed path spanning clients, Kafka, workers, and ClickHouse cannot claim global exactly-once delivery.

## Decision

The receiver acknowledges clients only after Kafka acknowledges. Workers advance Kafka offsets only after ClickHouse acknowledges all resulting writes. The system is at-least-once; replay duplicates are handled through ClickHouse deduplication, content-derived tokens where necessary, and worker state.

## Consequences

Consumers and writers must preserve partition provenance and settlement discipline. Duplicate-tolerant storage and aggregation design are required for all new pipelines.
