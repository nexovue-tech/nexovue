# Local and deployment architecture

## Current state

Docker Compose powers local development and a production-replica configuration. `just dev` starts infrastructure and services, creates Kafka topics, migrates both stores, seeds development data, and starts the OTel demo. Backup, restore, and verification scripts exist under `deploy/backup/`.

The on-premise Compose definition is intentionally a **skeleton, not yet runnable**. It has unresolved registry, sizing, web-assets/Caddy, browser-worker image, backup-storage, and public-gRPC decisions. It must not be described as a deployable production target.

## Required production properties

Before deployment, establish immutable images and registry ownership, configuration/secrets delivery, capacity sizing, backup schedule and restore drill, release/rollback process, monitoring and alerting, public endpoint/TLS behavior, and a staging verification path.
