# Database architecture

## Current verified behavior

Postgres holds control-plane state: organizations, users, sessions, projects, API/site keys, plans and billing arrangements, checks, integrations, deploy events, source-map metadata, usage, audit log, and alerting — notification channels (secrets sealed per ADR-010), rules, per-subject evaluation positions, incidents, the notifications outbox and its delivery log (ADR-013), per-service last-seen times written by the usage meter (from every door but the browser's), per-day usage including profile samples (shown, not billed while profiling is in preview), and the error groups each project has seen.

Anomaly detection (F6, migrations 0041 to 0049) added:

- `findings` (ADR-022), one row per detected problem, at most one open per service and one per project; `finding_incidents`, its links to threshold and *went silent* incidents;
- `detection_state`, where each service's judging stands, moved by compare-and-set; `detection_project_runs`, when each project's job last completed, when detection began expecting it, and the project's vote for the install-wide guard; `detection_baselines`, the hourly normal; `baseline_shifts`, a normal moved by a person or by adoption; `service_detection_settings`, a service's mute and snooze;
- `projects.timezone` (server default `'UTC'`) and `projects.detection_sensitivity`;
- `notifications.finding_id`: a notification's subject is an incident, a finding, or, for *detection stopped* and *running again*, the project itself, with a check that holds each row to one, and `notifications.dedup_key`, the PagerDuty alert a row went to;
- three install-wide tables with no `org_id`, because they describe nexovue rather than a customer: `intake_refusals` (written by the receiver), `detection_events` and `detection_sweep` (one row). `organizations/tables.py` excludes them from tenant deletion, with that reason (F5's `fix_sweep_status` joined them later).

Every other new table carries `org_id` and is deleted with its project.

Reliability targets (F19; migrations 0052, 0053, 0058 to 0060, 0063 and 0064, numbered as merged; ADR-027, ADR-028, ADR-034, ADR-038) added:

- `slo_targets`, a target's definition, soft-deleted for 30 days, with two partial unique indexes over live rows (the name and the definition, so two targets never read alike); `slo_status`, one row per target rewritten each minute by the judging job (`evaluated_minute`, the three burn tiers, `burning_since`); `slo_daily_counts`, one row per target and local day, which is why a month's figure outlives the rollups it was counted from (ADR-027); `slo_target_changes`, kept after a purge; `slo_deleted_months`, the months of a purged target; `slo_exclusions`, stretches left out with a reason;
- on `incidents`: `about_key`, `about_signal`, `about_endpoint` and `about_check_id`, so an incident can say what it is about, which a burn is (ADR-034); on `notifications`: `withheld_why`, the subject that held a row back, and `released_at` (ADR-038), with a partial index serving the rows held and not yet released;
- every one of these has `org_id`; the downgrades of the migrations holding figures that exist nowhere else refuse, and their revisions are on `ROLL_FORWARD_ONLY`. Past ones are a restore. `slo_daily_counts` is kept 13 months on every plan, then pruned by the scheduler.

Deployment and release tracking (F28; `0054`, `0057`, `0074`, `0075`, numbered as merged) added:

- `releases` (ADR-029), one row per project, service and version, telling what reported it and what was counted, and `release_periods`, each stretch one served, with `last_reading` (the comparison's words and counts stored 30 minutes in, never a name) and `mixed_environments` (FR45), which `/releases` and the Deploys section read so neither touches ClickHouse; `release_sync_state`, how far the release sync has read each project's version rollup; `deploy_intake_state`, what each project's webhook last received;
- on `deploy_events` (ADR-033): the signed `delivery_id`, the sender's `state`, `commit_sha` and `url`, the release it filed under and the sealed secret that signed it, each with `ON DELETE SET NULL` so a deploy outlives both; secrets are sealed (`project_webhook_secrets.sealed_secret`) and shown once;
- `notifications.release_period_id` (ADR-066): a release summary's subject, a third beside the incident and the finding, held to one subject by `names_one_subject` and to its own kind by `release_summary_names_its_period`, with its own unique key; the project key excludes rows naming a period;
- `projects.release_summary_channel_id` (D53), the channel people read that gets each release's summary, null by default, set to null when the channel is deleted.

Deploys and releases are deleted past the project's derived retention, measured by arrival and never the sender's time (`nexovue_scheduler/releases/retention.py`), and with a project or organization deleted for longer than the shortest plan's derived retention.

Suggested fixes (F5; migrations `0077`, `0079`, `0081` to `0085`) added:

- `finding_fixes`, one row per suggestion of either kind (`general`, `source`), with a frozen state
  and reason vocabulary, at most one in flight per finding (`uq_finding_fixes_one_working`) and one
  open draft (`uq_finding_fixes_one_draft`), and row security letting `nexovue_insight` change a
  row only while it is `proposing`. Its stored code (files read, edits, the requester's note) is
  emptied by one statement, `clear_stored_code`, at every end BR9 names, and by a restore;
- `finding_fix_source_packs` (the files sent, deleted when a run ends, through `fix_drop_pack`),
  `finding_fix_checks` (a person's check run and its patch, 15 minutes), `finding_fix_code_views`;
- `service_code_locations` (a service's repository and folder, ADR-106), `fix_source_consents`
  (an owner's agreement per repository and folder, bound to provider, key and model, ADR-105),
  `fix_repository_settings` (an owner's draft choices), `github_user_links` and
  `github_link_attempts`; on F22's `connections`, the App's client id, sealed secret and grants;
- `fix_sweep_status` (`0085`), one install-wide row: when rung 3's sweep last finished a pass.

The packs, check runs and link attempts are short-lived and are not in a backup's dump. Every
other F5 table carries `org_id`.

Migration 0037 (ADR-020) added ten logged tables for what Redis used to hold: `sessions`, `one_time_tokens`, `view_as_tickets`, `totp_enrolments`, `totp_challenges`, `totp_spent_codes`, `jobs` (the scheduler's queue), `job_results`, `alert_evaluation_status` and `rate_limit_windows`. Every short-lived row carries `expires_at`, reads filter on it, and the scheduler's expiry sweep (`sweep_expired`, every 60 seconds) deletes lapsed rows. They are deliberately not `UNLOGGED`: a crash or failover would empty them. The same sweep deletes what anomaly detection keeps only for a while (`nexovue_scheduler/detection/retention.py`): closed findings once their project's derived retention has passed, because they name services and endpoints, and `intake_refusals`, `detection_events` and `baseline_shifts` after the 30 days the baseline job reads back.

Schema changes use Alembic migrations in `apps/api/migrations/`.

ClickHouse stores raw telemetry and derived rollups, including profile samples (`profile_samples`, retention capped at 14 days, F17). F6 added `service_stats_1m` (each service's exact per-minute counts and latency histogram, written by the worker before sampling), `detection_readings` (each judged minute, frozen, written only by the scheduler's `nexovue_detection_writer`, ADR-023), both kept the lesser of the derived retention and 35 days, and `span_derived_progress`, a KeeperMap table of how far each span partition is counted, which holds no tenant data. F28 added `service_versions_1m` (`000038`, ADR-025): `service_stats_1m` split by `service.version`, from the same spans and summing to it per service-minute, with versions past its caps folded into `__other__`; the worker writes it last and a failure there holds no progress. `services_over_cap_1m` (`000039`, `docs/changes/browser-door`) records each tenant-minute the per-tenant service cap left services out of, by tenant and minute, with no project because the cap is per organization. `spans` and `logs` carry `door` (`000041`, `000042`, F28 D38): `browser` for a row from the browser door, `server` for every other, `''` for a row written before; the release page's errors by version read no browser row for a release counted in server traffic. ClickHouse migrations live under `deploy/clickhouse/migrations/`; workers use `ch-go` for writes. Kafka/ClickHouse delivery is at-least-once and writers use storage deduplication or content-derived tokens where required.

SeaweedFS in development, or S3-compatible storage in the deployment configuration, holds source-map bytes. There is no Redis: the scheduler's beat leader is chosen by a Postgres advisory lock.

Migrations, backup/restore tooling, retention, tenant erasure, and usage metering are detailed in the reference document and operations docs.
