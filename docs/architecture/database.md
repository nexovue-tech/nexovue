# Database architecture

## Current verified behavior

Postgres holds control-plane state: organizations, users, sessions, projects, API/site keys, plans and billing arrangements, checks, integrations, deploy events, source-map metadata, usage, audit log, and alerting — notification channels (secrets sealed per ADR-010), rules, per-subject evaluation positions, incidents, the notifications outbox and its delivery log (ADR-013), per-service last-seen times written by the usage meter, per-day usage including profile samples (shown, not billed while profiling is in preview), and the error groups each project has seen.

Migration 0037 (ADR-020) added ten logged tables for what Redis used to hold: `sessions`, `one_time_tokens`, `view_as_tickets`, `totp_enrolments`, `totp_challenges`, `totp_spent_codes`, `jobs` (the scheduler's queue), `job_results`, `alert_evaluation_status` and `rate_limit_windows`. Every short-lived row carries `expires_at`, reads filter on it, and the scheduler's expiry sweep (`sweep_expired`, every 60 seconds) deletes lapsed rows. They are deliberately not `UNLOGGED`: a crash or failover would empty them.

Schema changes use Alembic migrations in `apps/api/migrations/`.

ClickHouse stores raw telemetry and derived rollups, including profile samples (`profile_samples`, retention capped at 14 days, F17). ClickHouse migrations live under `deploy/clickhouse/migrations/`; workers use `ch-go` for writes. Kafka/ClickHouse delivery is at-least-once and writers use storage deduplication or content-derived tokens where required.

SeaweedFS in development, or S3-compatible storage in the deployment configuration, holds source-map bytes. There is no Redis: the scheduler's beat leader is chosen by a Postgres advisory lock.

Migrations, backup/restore tooling, retention, tenant erasure, and usage metering are detailed in the reference document and operations docs.
