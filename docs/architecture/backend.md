# Backend and data-plane architecture

## Current verified behavior

The receiver is a Go service that accepts OTLP over HTTP and gRPC plus browser OTLP under `/browser`. It authenticates a secret server key or public site key, applies the fixed gate order, normalizes accepted payloads into the protobuf envelope, and synchronously publishes to Kafka.

Workers are a Go binary with trace-assembly, log-writing, and metric-writing pipelines. They decode an envelope, mask its payload, derive pre-sampling span metrics, tail-sample traces where applicable, insert ClickHouse rows and rollups, then commit Kafka offsets only after all writes acknowledge.

The FastAPI API owns the Postgres control plane and reads tenant-scoped telemetry from ClickHouse. It does not participate in the ingestion path. Scheduler workers claim jobs from the Postgres `jobs` table (ADR-020): they run API, multistep, TCP, DNS and heartbeat checks and CloudWatch/Postgres pollers, encode results as envelopes, and publish directly to Kafka.

## Boundaries

`apps/api` is the only Postgres schema owner. Receiver and workers read configuration and identity but do not use Postgres as a general write path: off the request path, the workers write usage and the first-traces count, and the receiver a key's last use and the minutes Kafka refused its exports (`intake_refusals`). The scheduler writes integration health, its own job queue, alerting state and the outbox (F21), and findings and detection state (F6). See `current-system-reference.md` for pipeline details.
