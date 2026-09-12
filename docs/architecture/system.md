# Current system architecture

## Status

**Current state, verified 2026-09-12.** This document describes implemented system behavior. Planned insight/AI capabilities are excluded unless explicitly called out as future work.

## System boundary

Nexovue is a multi-tenant observability platform. It accepts OpenTelemetry traces, logs, metrics, browser RUM, synthetic checks, and cloud/database polling results; stores telemetry in ClickHouse; and serves a React console through a FastAPI control plane.

## Components

| Component | Responsibility |
|---|---|
| `apps/receiver` | OTLP HTTP/gRPC and browser ingestion, authentication, gates, envelope creation, Kafka publish |
| `apps/workers` | Kafka consumption, masking, derivation, sampling, storage, usage metering |
| `apps/api` | Postgres control-plane owner and tenant-scoped telemetry API |
| `apps/scheduler` | Celery-based synthetic probes and CloudWatch/Postgres pollers |
| `apps/web` | React console |
| `packages/contracts` | Frozen internal protobuf envelope |

## Data stores

Kafka is the durable buffer. ClickHouse stores telemetry and rollups. Postgres owns organizations, users, projects, credentials, billing, checks, integrations, deploy events, and audit state. Redis/Valkey stores sessions, rate-limit state, Celery broker state, and scheduler leadership. SeaweedFS or S3 stores source-map bytes.

## Non-negotiable delivery behavior

The receiver replies successfully only after Kafka acknowledges a record. Workers commit Kafka offsets only after ClickHouse acknowledges all resulting writes. The system is at-least-once, with deduplication where replay requires it. Metrics derived from spans happen before tail sampling.

See the [source-derived detailed reference](current-system-reference.md) for pipeline-level depth and the [decision records](../decisions/README.md) for historical reasoning.
