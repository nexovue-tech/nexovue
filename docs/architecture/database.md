# Database architecture

## Current verified behavior

Postgres holds control-plane state: organizations, users, sessions, projects, API/site keys, plans and billing arrangements, checks, integrations, deploy events, source-map metadata, usage, audit log, and alerting — notification channels (secrets sealed per ADR-010), rules, per-subject evaluation positions, incidents, the notifications outbox and its delivery log (ADR-013), per-service last-seen times written by the usage meter, per-day usage including profile samples (shown, not billed while profiling is in preview), and the error groups each project has seen.

Anomaly detection (F6, migrations 0041 to 0049) added:

- `findings` (ADR-022), one row per detected problem, at most one open per service and one per project; `finding_incidents`, its links to threshold and *went silent* incidents;
- `detection_state`, where each service's judging stands, moved by compare-and-set; `detection_project_runs`, when each project's job last completed, when detection began expecting it, and the project's vote for the install-wide guard; `detection_baselines`, the hourly normal; `baseline_shifts`, a normal moved by a person or by adoption; `service_detection_settings`, a service's mute and snooze;
- `projects.timezone` (server default `'UTC'`) and `projects.detection_sensitivity`;
- `notifications.finding_id`: a notification's subject is an incident, a finding, or, for *detection stopped* and *running again*, the project itself, with a check that holds each row to one, and `notifications.dedup_key`, the PagerDuty alert a row went to;
- three install-wide tables with no `org_id`, because they describe nexovue rather than a customer: `intake_refusals` (written by the receiver), `detection_events` and `detection_sweep` (one row). `organizations/tables.py` excludes them from tenant deletion, with that reason.

Every other new table carries `org_id` and is deleted with its project.

Migration 0037 (ADR-020) added ten logged tables for what Redis used to hold: `sessions`, `one_time_tokens`, `view_as_tickets`, `totp_enrolments`, `totp_challenges`, `totp_spent_codes`, `jobs` (the scheduler's queue), `job_results`, `alert_evaluation_status` and `rate_limit_windows`. Every short-lived row carries `expires_at`, reads filter on it, and the scheduler's expiry sweep (`sweep_expired`, every 60 seconds) deletes lapsed rows. They are deliberately not `UNLOGGED`: a crash or failover would empty them. The same sweep deletes what anomaly detection keeps only for a while (`nexovue_scheduler/detection/retention.py`): closed findings once their project's derived retention has passed, because they name services and endpoints, and `intake_refusals`, `detection_events` and `baseline_shifts` after the 30 days the baseline job reads back.

Schema changes use Alembic migrations in `apps/api/migrations/`.

ClickHouse stores raw telemetry and derived rollups, including profile samples (`profile_samples`, retention capped at 14 days, F17). F6 added `service_stats_1m` (each service's exact per-minute counts and latency histogram, written by the worker before sampling), `detection_readings` (each judged minute, frozen, written only by the scheduler's `nexovue_detection_writer`, ADR-023), both kept the lesser of the derived retention and 35 days, and `span_derived_progress`, a KeeperMap table of how far each span partition is counted, which holds no tenant data. ClickHouse migrations live under `deploy/clickhouse/migrations/`; workers use `ch-go` for writes. Kafka/ClickHouse delivery is at-least-once and writers use storage deduplication or content-derived tokens where required.

SeaweedFS in development, or S3-compatible storage in the deployment configuration, holds source-map bytes. There is no Redis: the scheduler's beat leader is chosen by a Postgres advisory lock.

Migrations, backup/restore tooling, retention, tenant erasure, and usage metering are detailed in the reference document and operations docs.
