# Local and deployment architecture

## Current state

Docker Compose powers local development and a production-replica configuration. `just dev` starts infrastructure and services, creates Kafka topics, migrates both stores, seeds development data, and starts the OTel demo. Backup, restore, and verification scripts exist under `deploy/backup/`.

The on-premise Compose definition is intentionally a **skeleton, not yet runnable**. It has unresolved registry, sizing, web-assets/Caddy, browser-worker image, backup-storage, and public-gRPC decisions. It must not be described as a deployable production target.

The scheduler runs three kinds of process: `scheduler-beat` (two replicas, one leader), `scheduler-light`
(probes and alert evaluation; it alone is handed `CLICKHOUSE_DSN`), and `scheduler-deliveries` (alert delivery
only, so a slow receiver never holds a probe's slot). All three read `EMAIL_*` and `APP_BASE_URL`.

The worker runs as two services: `worker` (spans, logs, metrics) and `worker-profiles` (profile ingest and
its meter, 512 MiB), so a profile flood cannot take the other signals' process down with it (F17, NFR1). A
deployment that sets `WORKER_PIPELINES=all` on one service puts them back together, and
`test_compose_signal_isolation.py` fails on it.

## Required production properties

Before deployment, establish immutable images and registry ownership, configuration/secrets delivery, capacity sizing, backup schedule and restore drill, release/rollback process, monitoring and alerting, public endpoint/TLS behavior, and a staging verification path.
