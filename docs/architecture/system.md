# Current system architecture

## Status

**Current state, verified 2026-09-12; profiles added 2026-09-19; anomaly detection added 2026-11-01.** This document describes implemented system behavior. Planned insight/AI capabilities are excluded unless explicitly called out as future work.

## System boundary

Nexovue is a multi-tenant observability platform. It accepts OpenTelemetry traces, logs, metrics, profiles (preview), browser RUM, synthetic checks, and cloud/database polling results; stores telemetry in ClickHouse; and serves a React console through a FastAPI control plane.

## Components

| Component | Responsibility |
|---|---|
| `apps/receiver` | OTLP HTTP/gRPC and browser ingestion, authentication, gates, envelope creation, Kafka publish; records the minutes the broker refused exports (F6) |
| `apps/workers` | Kafka consumption, masking, derivation, sampling, storage, usage metering; the per-service rollup and its progress marks (F6) |
| `apps/api` | Postgres control-plane owner and tenant-scoped telemetry API |
| `apps/scheduler` | Synthetic probes and CloudWatch/Postgres pollers; alert evaluation and delivery (F21); anomaly detection (F6) |
| `apps/web` | React console |
| `packages/contracts` | Frozen internal protobuf envelope, and `ProfileSamples` — the one payload of nexovue's own (ADR-015) |

## Data stores

Kafka is the durable buffer. ClickHouse stores telemetry and rollups, and detection's frozen readings. Postgres owns organizations, users, projects, credentials, billing, checks, integrations, deploy events, and audit state; since ADR-020 it also holds sessions, one-time links, second-factor state, the auth tier's rate-limit windows, the scheduler's job queue (`jobs`) and job results (Run now and send-test outcomes), when each alert sweep last ran, and beat leadership as an advisory lock. Since F6 it holds findings and the state detection judges from. Other rate limits are counted in each process. SeaweedFS or S3 stores source-map bytes.

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
- **Profiles are held back at the door, not in the worker** (NFR1, AC15, amended 2026-09-20): their
  own rate bucket and a per-key samples-per-second budget at the receiver, their own topic, and a
  4 MiB bound on what the worker fetches at once. They ran in a worker process of their own from
  2026-09-19 to 2026-09-20; with one worker again, **a profile flood that exhausts its memory stops
  the other signals with it** until Kafka's backlog drains. Measured on 2026-09-19 and accepted
  knowingly — `docs/changes/one-worker-again/`.
- Stored in `profile_samples`, read by `/profiles/*`, shown on the Profiles screen, a span's *Profile*
  section and the service page's Profiling strip.

## Anomaly detection (F6)

Added 2026-11-01; the design is
[`features/anomaly-detection/design.md`](../features/anomaly-detection/design.md), the runbook
[`operations/detection.md`](../operations/detection.md).

- **The worker counts every service, before sampling**, into `service_stats_1m`: exact requests, failures and
  a 64-bucket latency histogram per service-minute. After ClickHouse takes a settlement's inserts, it marks
  how far each span partition is counted in `span_derived_progress` (KeeperMap).
- **A minute is judged only once it is settled**: every span partition is counted past it, plus the 2-minute
  export allowance. Two things hold a minute for every project, because they say nexovue's own intake failed:
  a minute the receiver recorded refusing exports because Kafka would not take them (`intake_refusals`), and
  the install-wide guard, at least 5 organizations losing traffic in the minutes the span topic's record
  count fell. A hold lasts at most 30 minutes.
- **The scheduler judges, on its own `detection` queue** in `scheduler-light`, at most 2 jobs at once. Beat's
  `sweep:detection` settles a minute each minute and queues one job per project; `sweep:detection-baselines`
  rebuilds each project's normal hourly. A project job judges its services, freezes what it saw in
  ClickHouse's `detection_readings`, and commits findings and their notifications in one Postgres
  transaction.
- **`detection_readings` is written through an account that can only insert into it**
  (`nexovue_detection_writer`, ADR-023), and only from `nexovue_scheduler.detection.store`.
- **A finding is its own record, not an incident** (ADR-022). The outbox's subject is an incident, a finding,
  or the project itself (for *detection stopped* and *running again*). The webhook body is version 2.
- **Operators see it stall**: beat's leader publishes install-wide gauges with no project label, and three
  Prometheus alerts page on a stalled sweep, a project behind, and a hold by nexovue's intake.

## Non-negotiable delivery behavior

The receiver replies successfully only after Kafka acknowledges a record. Workers commit Kafka offsets only after ClickHouse acknowledges all resulting writes. The system is at-least-once, with deduplication where replay requires it. Metrics derived from spans happen before tail sampling.

See the [source-derived detailed reference](current-system-reference.md) for pipeline-level depth and the [decision records](../decisions/README.md) for historical reasoning.
