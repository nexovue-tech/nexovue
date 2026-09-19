# Current system architecture

## Status

**Current state, verified 2026-09-12; profiles added 2026-09-19.** This document describes implemented system behavior. Planned insight/AI capabilities are excluded unless explicitly called out as future work.

## System boundary

Nexovue is a multi-tenant observability platform. It accepts OpenTelemetry traces, logs, metrics, profiles (preview), browser RUM, synthetic checks, and cloud/database polling results; stores telemetry in ClickHouse; and serves a React console through a FastAPI control plane.

## Components

| Component | Responsibility |
|---|---|
| `apps/receiver` | OTLP HTTP/gRPC and browser ingestion, authentication, gates, envelope creation, Kafka publish |
| `apps/workers` | Kafka consumption, masking, derivation, sampling, storage, usage metering |
| `apps/api` | Postgres control-plane owner and tenant-scoped telemetry API |
| `apps/scheduler` | Celery-based synthetic probes and CloudWatch/Postgres pollers; alert evaluation and delivery (F21) |
| `apps/web` | React console |
| `packages/contracts` | Frozen internal protobuf envelope, and `ProfileSamples` — the one payload of nexovue's own (ADR-015) |

## Data stores

Kafka is the durable buffer. ClickHouse stores telemetry and rollups. Postgres owns organizations, users, projects, credentials, billing, checks, integrations, deploy events, and audit state. Redis/Valkey stores sessions, rate-limit state, Celery broker state, scheduler leadership, send-test outcomes and when each alert sweep last ran. SeaweedFS or S3 stores source-map bytes.

## Alerts (F21)

Added 2026-09-19; the design is [`features/alerts/design.md`](../features/alerts/design.md).

- **A check's state change is judged in the transaction that records it** (`probes/state_store.py` →
  `alerts/check_state.py`): the incident and its notifications commit with the state or not at all.
- **The service conditions are judged every minute by beat** on the `light` queue: error rate and p95 from
  ClickHouse's `endpoint_stats_1m`, new error groups from `spans` and `logs`, silence from Postgres's
  `service_last_seen`, which the Go usage meter writes. The scheduler reads ClickHouse through its own
  tenant-scoped reader (`alerts/telemetry.py`), held to the API's chokepoint rules (ADR-012).
- **Notifications are a Postgres outbox** (ADR-013), delivered by the `scheduler-deliveries` worker on its own
  `deliveries` queue, through the prober's SSRF-guarded client. Leases, not locks, cover a send; one batch per
  channel is in flight at a time.

## Profiles (F17, preview)

Added 2026-09-19; the design is [`features/profiling/design.md`](../features/profiling/design.md).

- **The unstable format stops at the door** (ADR-015): the receiver converts OTLP profiles
  `v1development` into `nexovue.v1.ProfileSamples`, splits it into records under the broker's limit, and
  nothing behind it reads OTLP profiles.
- **Profiles cannot delay the other signals** (NFR1, AC15): their own rate bucket and a per-key
  samples-per-second budget at the door, their own topic, and their own worker process
  (`worker-profiles`) — a profile flood that kills that process stops profiles alone. Measured
  2026-09-19; see the feature's `status.md`.
- Stored in `profile_samples`, read by `/profiles/*`, shown on the Profiles screen, a span's *Profile*
  section and the service page's Profiling strip.

## Non-negotiable delivery behavior

The receiver replies successfully only after Kafka acknowledges a record. Workers commit Kafka offsets only after ClickHouse acknowledges all resulting writes. The system is at-least-once, with deduplication where replay requires it. Metrics derived from spans happen before tail sampling.

See the [source-derived detailed reference](current-system-reference.md) for pipeline-level depth and the [decision records](../decisions/README.md) for historical reasoning.
