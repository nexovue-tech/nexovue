# Database architecture

## Current verified behavior

Postgres holds control-plane state: organizations, users, sessions in Redis-backed form, projects, API/site keys, plans and billing arrangements, checks, integrations, deploy events, source-map metadata, usage, and audit log. Schema changes use Alembic migrations in `apps/api/migrations/`.

ClickHouse stores raw telemetry and derived rollups. ClickHouse migrations live under `deploy/clickhouse/migrations/`; workers use `ch-go` for writes. Kafka/ClickHouse delivery is at-least-once and writers use storage deduplication or content-derived tokens where required.

SeaweedFS in development, or S3-compatible storage in the deployment configuration, holds source-map bytes. Redis/Valkey holds sessions, rate-limit state, Celery broker state, and scheduler leader-election locks.

Migrations, backup/restore tooling, retention, tenant erasure, and usage metering are detailed in the reference document and operations docs.
