# Local and deployment architecture

## Current state

Docker Compose powers local development and a production-replica configuration. `just dev` starts infrastructure and services, creates Kafka topics, migrates both stores, seeds development data, and starts the OTel demo. Backup, restore, and verification scripts exist under `deploy/backup/`.

The on-premise Compose definition is intentionally a **skeleton, not yet runnable**. It has unresolved registry, sizing, web-assets/Caddy, browser-worker image, backup-storage, and public-gRPC decisions. It must not be described as a deployable production target.

The scheduler runs three kinds of process: `scheduler-beat` (two replicas, one leader), `scheduler-light`
(probes, alert evaluation, the nightly erasure, and anomaly detection on its `detection` queue, at most 2 jobs
at once; it alone is handed `CLICKHOUSE_DSN`, `CLICKHOUSE_ERASER_DSN` and `CLICKHOUSE_DETECTION_WRITER_DSN`),
and `scheduler-deliveries` (alert delivery only, so a slow receiver never holds a probe's slot). All three read
`EMAIL_*` and `APP_BASE_URL`.

The worker runs as one service carrying every pipeline — spans, logs, metrics and profiles — with the
memory the two services it replaced carried between them (1280 MiB in deployment, `GOMEMLIMIT` at ~85% of
it). `WORKER_PIPELINES` is what splits it: give a second service the same image and a narrower list, and
halve this one's memory limit to pay for it. Profiles ran that way from 2026-09-19 to 2026-09-20
(`docs/changes/one-worker-again/`); what a single process costs is that a flood which exhausts the cap
stops every signal until the backlog drains. `test_compose_worker_memory.py` holds the heap ceiling and
the container limit together, which is the part that keeps the kernel out of it.

## Required production properties

Before deployment, establish immutable images and registry ownership, configuration/secrets delivery, capacity sizing, backup schedule and restore drill, release/rollback process, monitoring and alerting, public endpoint/TLS behavior, and a staging verification path.
