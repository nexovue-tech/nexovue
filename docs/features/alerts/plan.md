# Plan: Alerts & notification connectors (F21)

> **Status:** Approved 2026-09-19 — Q1 and Q3 as assumed, Q2 answered *A* · In progress
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order; tasks under
> each step name the file that changes and the test that proves it, read from the code. **The founder approves
> this plan before any file is edited.**

## Preconditions

- Specification approved 2026-09-18, amended 2026-09-19 with the design ([`spec.md`](spec.md)).
- Design approved 2026-09-19 ([`design.md`](design.md)), with
  [ADR-012](../../decisions/ADR-012-the-scheduler-evaluates-alerts.md),
  [ADR-013](../../decisions/ADR-013-notifications-are-an-outbox.md) and the
  [ADR-010](../../decisions/ADR-010-check-secrets.md) amendment.
- **The compose-dependencies change is committed first** (`docs/changes/compose-dependencies/`). It is done and
  waiting for your review; Steps 1 and 5 edit the same three compose files and the same rule test, and two
  unreviewed changes in one file would be one diff you cannot read apart.
- Q1–Q3 below are non-blocking; each proceeds on its stated assumption unless you say otherwise.

## Current system

Read from the code on 2026-09-19. **Five places disagree with the approved design**, marked ⚠. None changes a
requirement; each changes a task, and each is carried into *Questions* or resolved in the task that meets it.

### Scheduler: where check alerts start

| What | Where |
|---|---|
| `record()` opens one transaction (`engine.begin()`), retries once on `IntegrityError` | `apps/scheduler/src/nexovue_scheduler/probes/state_store.py:76-100` |
| `_apply()` locks `check_states FOR UPDATE`, writes the state and the `check_state_changes` row, returns the new state — **the hook point** | `probes/state_store.py:103-122` |
| Only `up`, `degraded`, `down`, `misconfigured` are states; a late run changes nothing | `probes/state.py:15, 33-42` |
| `record_outcome` task: 5 retries with backoff; logs the change | `probes/tasks.py:173-207` |
| One engine per worker process on its own event loop (`pool_size=1`) | `probes/tasks.py:483-496` |
| Scheduler SQL is plain `text()` over named columns; the API owns the tables | `checks.py:1-12`, `probes/state_store.py:8-9` |

### Scheduler: sweeps, queues, outbound HTTP

| What | Where |
|---|---|
| Periodic sweeps are fixed entries rebuilt with the schedule; `light` queue, `expires`, soft/hard limits 50/58 s | `beat.py:47-54, 307-331` |
| Queues: `light` and `light.<location>`, each on its own exchange; no result backend; `task_acks_late` | `celery_app.py:31-64` |
| Task routes; task modules imported | `celery_app.py:64, 72-77` |
| ⚠ **`scheduler-light` has no `-Q`**, so it consumes every queue in `task_queues`. Adding `light.deliveries` there puts deliveries on the same eight slots as probes — the isolation NFR5 asks for would be nominal | `deploy/compose/docker-compose.yaml:390-397`, `celery_app.py:41-44` → Q1 |
| `perform(PreparedRequest)` already sends any method with headers and a body through `ConnectGuard`, never raises for the target's behaviour, caps the response at 1 MiB — **reusable as the delivery client unchanged** | `probes/http_check.py:58-66, 99-135` |
| `ConnectGuard` refuses non-public addresses at connect time and reports why | `probes/addresses.py:124-150` |
| ⚠ **`reportable_url` does not remove a secret in the path.** It strips userinfo and signed query parameters; a Slack, Discord or Teams webhook URL carries its credential in the path. The design said to log channel URLs through it — that would log the secret | `probes/urls.py:39-58` → Step 2, T2.3 |
| Run now stores its result in Redis under a tenant-first key, 300 s TTL; the API polls it | `packages/py-platform/.../checks/task.py:28-31, 62-64`, `apps/api/.../checks/run_now.py:44-72` |
| Settings: `check_secret_key` present; **no `clickhouse_dsn`, no email settings** | `settings.py:29-129` |
| The scheduler's dependencies: pycurl, SQLAlchemy/asyncpg, no ClickHouse driver, no httpx | `apps/scheduler/pyproject.toml` |

### Sealing, email

| What | Where |
|---|---|
| `SealedBox(key, purpose=…)`; associated data `purpose:owner`; `CHECK_SECRET_PURPOSE` | `packages/py-platform/src/nexovue_platform/checks/sealing.py:33-81` |
| `EmailSender` protocol, closed `Message` enum (three members), one address per call | `apps/api/src/nexovue_api/notifications/sender.py:39-90` |
| Templates are repository-owned and render-or-refuse | `notifications/templates.py` |
| Resend adapter uses **httpx** (async); console adapter renders and logs | `notifications/resend.py:41-80`, `notifications/console.py:46-67` |
| `build_email_sender` is the composition root; `EmailSettings` and `EmailProvider` live in API settings | `notifications/__init__.py:28-45`, `settings.py:109-130, 387-425` |
| Five API modules import the port | `deps.py:34`, `lifespan.py:34`, `auth/recovery.py:67`, `signup/router.py:26`, `profile/service.py:58` |
| `test_provider_ports.py` pins the port's path | `apps/api/tests/test_provider_ports.py:26-28` |
| `py-platform` has no httpx | `packages/py-platform/pyproject.toml` |

### Control plane

| What | Where |
|---|---|
| Latest migration `0027` | `apps/api/migrations/versions/0027_synthetic_checks_become_api_checks.py` |
| Models: org-scoped tables carry `org_id` FK `ondelete=CASCADE`; `CheckStateChange` already says *"what an alert will be raised from (F21)"* | `data/postgres/models.py:451-505` |
| Router pattern: reads `CurrentIdentity`, writes `MayAdminister`, service takes `org_id` and `actor_user_id` | `maintenance/router.py:22-60` |
| Audit: `<entity>.<past tense>` constants and `record_*` functions joining the caller's session | `audit/service.py:1-60, 69-72` |
| Tenant creation: organization, owner, default project, key, webhook secret, audit — one transaction | `signup/service.py:74-136` |
| Project creation | `projects/service.py:94-124` |
| `users.role`, `users.email_verified_at` — the owners channel's recipients | `data/postgres/models.py:163-177` |
| Tenant deletion enumerates tables: org-only tables are `DELETED_DIRECTLY`, project children `REMOVED_BY_CASCADE` | `organizations/tables.py:32-60` |
| Own rate tiers by path prefix | `ratelimit/policy.py:94-110, 194-200` |
| Routers are included in `main.py` | `main.py:112-143` |
| Postgres tenancy structure test walks **API source only** | `apps/api/tests/test_postgres_tenancy_structure.py:28-30` |

### Telemetry reads

| What | Where |
|---|---|
| Service error rate and p95 over `endpoint_stats_1m`, one template for list and detail | `services/queries.py:74-117`, `rollup.py:20-30` |
| Error-group identity: `sipHash64` of the SDK fingerprint or `exception_type + message_template`, computed at read time over `spans ∪ logs`, pruned by `received_at` | `errorgroups/queries.py:78-90, 116-154, 182-197` |
| The ClickHouse tenancy chokepoint: `TenantScopedRepository`, `binds_tenant`, `READ_SETTINGS` | `data/clickhouse/base.py:1-140` |
| ⚠ **The chokepoint and its structure test are API-only.** `test_tenant_chokepoint_structure.py` walks `apps/api/src` and forbids the raw driver outside `data/clickhouse/`. ADR-012 needs the scheduler to read ClickHouse under the same rule, so the chokepoint itself moves to `py-platform` rather than being copied | `tests/test_tenant_chokepoint_structure.py:29-30, 83, 271` → Step 5 |
| The API's ClickHouse driver is `clickhouse-connect[async]` | `apps/api/pyproject.toml:16` |

### Go usage meter

| What | Where |
|---|---|
| `Apply` advances offsets, adds counters, adds services — one transaction; a replayed flush returns before writing anything | `apps/workers/internal/pipeline/usage/postgres.go:194-240` |
| `addServices` inserts `(org, project, day, service)` joined to `projects` so no message writes into another tenant | `usage/postgres.go:134-153` |
| A `Row` carries a day, not a time; the flush's **receive time** is `Progress.ReceivedThrough` | `usage/meter.go:35-44`, `usage/postgres.go:37-47` |
| ⚠ **The meter already records how far it has read in receive time** (`usage_ingest_offsets.counted_through_received_at`). The design judged silence against the clock; judged against the clock, a meter that falls behind makes every service look silent at once — Q2 | `usage/postgres.go:82-96`, `data/postgres/models.py:857-875` → Q2 |

### Console

| What | Where |
|---|---|
| Main navigation and its manifest test | `apps/web/src/app/routes/route-manifest.ts:17-73`, `route-manifest.test.ts` |
| Router: one `createRoute` per screen | `app/router.tsx:76-390` |
| Settings rail; *This organization* group | `shared/settings/settings-nav.ts:8-80` |
| Every form that edits must be in the unsaved-changes coverage list | `shared/components/unsaved-changes-coverage.test.ts:36-50` |
| Reusable: `EditableRows`, `Tabs`, `SelectField`, `TextField`, `EmptyState`, `InlineFailure`, `Skeleton`, `Sparkline`, `TrendChart`, `UnsavedChanges`, `DataTable` | `shared/components/` |
| ⚠ **A feature never imports another feature**, and the design puts an *Alerts* strip on the check page (`features/synthetics`) and the service page (`features/servicedetail`). The strip therefore lives in `shared/alerts/`, beside `shared/settings/` | `docs/engineering/frontend.md`; → Step 3, T3.9 |
| E2E: Playwright in `tools/e2e/tests/`, against the running stack | `tools/e2e/tests/synthetics.spec.ts` |

## Change required

[`spec.md`](spec.md) and [`design.md`](design.md). Nothing is restated here.

## Impact

| Area | What changes |
|---|---|
| Files | New: `apps/api/src/nexovue_api/alerts/`, `.../channels/`; `apps/scheduler/src/nexovue_scheduler/alerts/`; `packages/py-platform/src/nexovue_platform/{email,alerts,clickhouse}/`; `apps/web/src/features/alerts/`, `shared/alerts/`. Changed: `probes/state_store.py`, `beat.py`, `celery_app.py`, scheduler `settings.py`, `signup/service.py`, `projects/service.py`, `main.py`, `audit/service.py`, `ratelimit/policy.py`, `organizations/tables.py`, `usage/postgres.go`, the API's `notifications/` (moves), `errorgroups/queries.py` and `services/queries.py` (shared fragments), `data/clickhouse/base.py` (moves), three compose files |
| Services | API, scheduler (beat, light, **new** deliveries worker), Go workers (meter) |
| Schema / migrations | Postgres `0028` (nine tables, two backfills), `0029` (`service_last_seen`, `error_groups_seen`). ClickHouse: none |
| UI | Alerts screen, rule editor, Channels list and detail, the Alerts strip on check and service pages, one navigation entry, one settings entry |
| Tests | ~30 new test modules (named per task); five structural tests extended |

## Reuse

- **Delivery HTTP**: `probes/http_check.perform` and `ConnectGuard`, unchanged. No second outbound client.
- **Sealing**: `SealedBox` with a new purpose. No new key, no new setting.
- **Email**: the existing port, templates and both adapters — moved, not rewritten.
- **Run now's pattern** for send test: `send_task` by name, result in Redis under a tenant-first key, API waits.
- **Beat's sweep entries** for the four new sweeps; **the one engine per worker** for their Postgres access.
- **Service error rate and p95**: the Services screen's own statement, shared — not a second definition.
- **Error-group fingerprint**: the Errors screen's own expression, shared.
- **The recording site** (`apps/scheduler/tests/recording_site.py`) as every channel kind's receiving endpoint in tests
  (AC1).
- **Console**: every component listed under *Console* above; `features/projects/ProjectSettings.tsx` for
  validation, dirty-against-stored and type-to-confirm delete; `features/auth/problem-fields.ts` for field errors.

## Gaps

- An alerts domain: rules, incidents, evaluation state, the outbox, the delivery log.
- Channel kinds and their six message formats.
- Standard Webhooks signing.
- A way to log a channel's destination without its secret.
- A ClickHouse client in the scheduler, under the tenancy chokepoint.
- A per-service last-seen time.
- A stored record of which error groups a project has seen.

## Steps and tasks

Each step ends with `just check` green on what it touched and the step's own judgeable result shown. Tests are
written with the code they prove, and each behavioural test is observed red before its code is written.

### Step 1 — A check goes down, and its owner gets an email

The thinnest whole path: F18's state change → the default rule → an incident → an outbox row → an email. No
console yet; everything after the state change is new, so every assumption the design makes about transactions,
the outbox and the scheduler's email is tested here, where it is cheapest to be wrong.

*Judgeable:* sign up a fresh organization, create a check against a URL that fails, and within a minute the
scheduler's log shows the rendered *opened* email to the owner (console provider). Fix the URL: a *resolved* email
with the duration. `psql` shows one incident, two notifications, two attempts.

- [x] **T1.1** — `packages/py-platform/src/nexovue_platform/email/{__init__,sender,templates,resend,console,settings}.py`
  · the port, templates, both adapters, `EmailSettings`/`EmailProvider` and `build_email_sender` move here unchanged;
  `py-platform` gains `httpx`. `apps/api/src/nexovue_api/notifications/` is deleted and its five importers (listed
  under *Current system*) import from the new module; the API's `settings.email` keeps building the dataclass ·
  proved by the existing `test_notifications*.py` and `test_provider_ports.py` (path updated) passing unchanged.
- [x] **T1.2** — `nexovue_platform/email/sender.py`, `templates.py` · `Message.ALERT` (subject and body supplied by
  the caller, rendered by the alerts module) and `Message.CHANNEL_FAILING` · proved by new cases in
  `apps/api/tests/test_notifications.py` (every member has a template; a missing field refuses).
- [x] **T1.3** — `apps/api/migrations/versions/0028_alerts.py` · `notification_channels`, `alert_rules`,
  `alert_rule_channels`, `alert_evaluations`, `incidents` (partial unique index on `(rule_id, subject_key) WHERE
  resolved_at IS NULL`), `notifications` (unique `(incident_id, channel_id, kind, sequence)`),
  `notification_attempts`; backfills one `owners` channel per organization and the default rule per project. The
  reasoning is written in the migration (`docs/templates/migration.md`) · proved by
  `apps/api/tests/test_alerts_migration_live.py` (upgrade, backfill counts, downgrade, upgrade again).
- [x] **T1.4** — `apps/api/src/nexovue_api/data/postgres/models.py` · the seven models · proved by
  `test_postgres_tenancy_structure.py` picking them up (it derives its list from the models).
- [x] **T1.5** — `apps/api/src/nexovue_api/organizations/tables.py` · `notification_channels` in `DELETED_DIRECTLY`
  (it hangs off the organization alone), the rest in `REMOVED_BY_CASCADE` · proved by `test_deletion_live.py`.
- [x] **T1.6** — `apps/api/src/nexovue_api/alerts/defaults.py` · `provision_organization(session, org)` (the owners
  channel) and `provision_project(session, project)` (the default rule), called from `signup/service.py:74-136`
  and `projects/service.py:94-124` inside their transactions · proved by `apps/api/tests/test_alert_defaults_live.py`
  — signup yields one of each (AC21); a second project gets its own rule; a deleted default rule is not recreated
  by anything (AC22).
- [x] **T1.7** — `packages/py-platform/src/nexovue_platform/alerts/model.py` · the shared vocabulary: condition
  kinds, severities, notification kinds, channel kinds, the incident subject key · proved by
  `apps/scheduler/tests/test_alert_model.py`.
- [x] **T1.8** — `apps/scheduler/src/nexovue_scheduler/alerts/incidents.py` · open / escalate / resolve an incident
  for `(rule, subject)` and write its outbox rows, on a connection it is given; mute writes no rows (AC16) · proved
  by `apps/scheduler/tests/test_alert_incidents_live.py`.
- [x] **T1.9** — `apps/scheduler/src/nexovue_scheduler/alerts/check_state.py` · `on_check_state_change(connection,
  change)`: the covering rules, the state → incident mapping, escalation (AC27), misconfigured apart from down (AC7)
  · proved by `apps/scheduler/tests/test_alert_check_state.py` (pure mapping) and `..._live.py` (AC5, AC7, AC27).
- [x] **T1.10** — `probes/state_store.py:103-122` · `_apply` calls `on_check_state_change` after `_INSERT_CHANGE`,
  in the same connection; `record()` returns what the caller needs to dispatch delivery · proved by
  `apps/scheduler/tests/test_state_store_alerts_live.py`: a failure inside the alert write rolls back the state
  change too (the design's all-or-nothing claim, tested rather than asserted).
- [x] **T1.11** — `apps/scheduler/src/nexovue_scheduler/alerts/outbox.py` · claim due rows `FOR UPDATE SKIP
  LOCKED` with a lease, **at most one in flight per channel**, record the attempt, schedule the retry (FR19) ·
  proved by `apps/scheduler/tests/test_alert_outbox_live.py` — two claimers never take one row; an expired lease is
  reclaimed; 503, 503, 200 is one delivery and three attempts (AC17).
- [x] **T1.12** — `apps/scheduler/src/nexovue_scheduler/alerts/messages.py` · the words of every message (FR16), with
  nothing from BR9 in them · proved by `apps/scheduler/tests/test_alert_messages.py`, including a check whose
  failure reason holds a response body and a header value that must not appear (AC24).
- [x] **T1.13** — `apps/scheduler/src/nexovue_scheduler/alerts/senders/email.py` · `email` and `owners` kinds through
  the moved port; owners resolved at send time to verified owners · proved by
  `apps/scheduler/tests/test_alert_email_sender.py`.
- [x] **T1.14** — `apps/scheduler/src/nexovue_scheduler/alerts/tasks.py`, `celery_app.py:41-77`, `beat.py:307-331` ·
  `deliver` (fast path, dispatched by `record_outcome` after commit), `sweep_deliveries` (15 s), `prune_attempts`
  (hourly); the `light.deliveries` queue and routes · proved by `apps/scheduler/tests/test_beat_schedule.py`
  (entries present) and `test_alert_tasks.py`.
- [x] **T1.15** — scheduler `settings.py` · the `EMAIL_*` settings, read exactly as the API reads them · proved by
  `apps/scheduler/tests/test_scheduler_settings.py`.
- [x] **T1.16** — `deploy/compose/docker-compose{,.app,.onprem}.yaml` · `EMAIL_*` for the scheduler services;
  **a `scheduler-deliveries` worker** consuming `light.deliveries` only, and `scheduler-light` excluding it (Q1) ·
  proved by `test_compose_dependencies.py` (its decided list updated) and a resolved-config diff.
- [x] **T1.17** — *(moved to Step 2: `deliver` is dispatched only by the scheduler itself; the first task the API dispatches is `send_test`, T2.8)* — `apps/api/tests/test_cross_service_contracts.py` · the `deliver` task's name and arguments held
  against the worker's signature, as Run now's are.

### Step 2 — Every channel kind, with send test

*Judgeable:* through the API (Swagger at `/docs`), create a Slack, Teams, Discord, PagerDuty and webhook channel
pointed at the recording site; *send test* on each returns the outcome, and the recording site shows each kind's
native format and, for the webhook, a signature that verifies. No response contains a secret.

- [x] **T2.1** — `packages/py-platform/src/nexovue_platform/checks/sealing.py:81` ·
  `NOTIFICATION_CHANNEL_PURPOSE` and `channel_secret_owner(org_id, channel_id)` · proved by
  `apps/scheduler/tests/test_channel_sealing.py` (new — no sealing test exists in the scheduler yet) — a value
  sealed for one channel does not open for another, nor as a check secret. *As built:* that test is
  `packages/py-platform/tests/test_alert_channels.py`, beside the sealing it tests.
- [x] **T2.2** — `apps/scheduler/src/nexovue_scheduler/alerts/senders/{slack,teams,discord,pagerduty,webhook}.py` ·
  payload per kind (FR17) sent through `http_check.perform` with redirects off and a 10 s timeout; the webhook signs
  with Standard Webhooks, `webhook-id` = notification id (FR4) · proved by `test_alert_payloads.py` (shape per
  kind, PagerDuty `dedup_key` = incident id on `trigger`, `resolve` and escalation — AC20) and
  `test_alert_senders_live.py` against the recording site (AC1, AC3 — the signature verified with an independent
  HMAC, the same `webhook-id` on a retry).
- [x] **T2.3** — `apps/scheduler/src/nexovue_scheduler/alerts/destinations.py` · `describe_destination(url)` → scheme
  and host only, for logs and the delivery log · proved by `test_alert_destinations.py`, with a Slack, Discord and
  Teams URL whose path secret must not survive (the ⚠ under *Current system*). *As built:*
  `nexovue_platform.alerts.channels.destination_host`, shared with the API, proved by `test_alert_channels.py`.
- [x] **T2.4** — `test_alert_senders_live.py` · a hostname resolving to a private or metadata address is refused and
  recorded as *"the destination is not a public address"*, with no address (AC4).
- [x] **T2.5** — `apps/api/src/nexovue_api/channels/{router,service,queries,schemas}.py`, `main.py` · CRUD, secret
  write-only and replaced never read, literal private address refused at save, the webhook secret generated and
  returned once, email refused when no provider is configured (the spec's error table), `owners` not deletable ·
  proved by `apps/api/tests/test_channels_live.py` (the schema refusals are cases there).
- [x] **T2.6** — `audit/service.py` · `notification_channel.created|updated|muted|deleted` · proved by
  `test_channels_live.py`: every change writes a row, no row contains the secret (AC26, AC2).
- [x] **T2.7** — `channels/send_test.py`, `ratelimit/policy.py:94-110` · dispatch `send_test` by name, wait up to 10 s
  for the outcome in Redis; its own tier · proved by `test_channels_live.py` (dispatched only for the caller's own
  channel; the outcome not readable by another organization) and `test_ratelimit_coverage.py`.
- [x] **T2.8** — `apps/scheduler/src/nexovue_scheduler/alerts/tasks.py` · `send_test` · proved by
  `test_cross_service_contracts.py` and `test_alert_send_test_and_health_live.py`.
- [x] **T2.9** — `alerts/health.py` (scheduler) · a channel failing for an hour is marked `failing` and the owners
  are emailed once through email; the next success heals it (FR21, AC18) · proved by
  `test_alert_send_test_and_health_live.py`.
- [x] **T2.10** — AC2 · *as built, without the one sweeping test:* `test_channels_live.py` searches every response
  and audit row it produces for the secrets it wrote; `test_alert_senders_live.py` proves a receiver echoing the URL
  does not put the credential in the delivery log. Log lines are not captured and searched.

### Step 3 — The console: Alerts, rules, channels

*Judgeable:* in the browser, from a failing check's page, *Alert me about this* → connect a channel inline → *send
test* → save. The Alerts screen shows the open incident; the channel's delivery log shows the attempt.

- [x] **T3.1** — `apps/api/src/nexovue_api/alerts/{router,service,queries,schemas}.py` · rules CRUD (with *covers
  nothing* and *not notifying anyone* derived), mute, incidents list and detail, deliveries per channel,
  `GET /alerts/evaluation` · proved by `apps/api/tests/test_alerts_live.py` (22) and `test_alert_vocabulary.py`.
  *As built:* no separate `test_alert_rules_schemas.py`; the schema refusals are cases in `test_alerts_live.py`.
- [x] **T3.2** — `audit/service.py` · `alert_rule.created|updated|muted|deleted` · proved by `test_alerts_live.py`
  (AC26).
- [x] **T3.3** — `test_authorization_coverage.py` · every new write is `MayAdminister` · proved by the test itself,
  which parses every router, so the new routes were judged without an edit.
- [x] **T3.4** — `apps/web/src/features/alerts/api.ts` · typed client for both routers · *as built:* proved
  through the screens' tests against a stubbed `fetch` (`render-harness.test-util.ts`), not a separate `api.test.ts`.
- [x] **T3.5** — `features/alerts/AlertsScreen.tsx` · open incidents, rules, recent; every state in the design's
  table · proved by `AlertsScreen.test.ts` (AC25).
- [x] **T3.6** — `features/alerts/RuleEditor.tsx` · the four sections in the design's order, defaults prefilled,
  inline *add channel* · proved by `RuleEditor.test.ts`. *As built:* the draft lives in `alert-words.ts`
  (`alert-words.test.ts`), not `rule-draft.ts`.
- [x] **T3.7** — `features/alerts/ChannelsScreen.tsx`, `ChannelDetailScreen.tsx`, `ChannelForm.tsx` · list with
  health, detail with send test and delivery log, secret shown as *set* with *replace*, quiet hours, deleting a
  channel names the rules that lose it (FR25) · proved by `ChannelsScreen.test.ts`. *As built:* the delivery log
  is part of `ChannelDetailScreen.tsx`.
- [x] **T3.8** — `app/router.tsx`, `app/routes/route-manifest.ts`, `shared/settings/settings-nav.ts` · `/alerts`,
  `/alerts/rules/new`, `/alerts/rules/$ruleId`, `/settings/channels`, `/settings/channels/$channelId` · proved by
  `route-manifest.test.ts`.
- [x] **T3.9** — `shared/alerts/AlertsStrip.tsx`, `shared/alerts/api.ts` · the strip on a check's page and a
  service's page (FR24) · proved by `CheckDetailScreen.test.ts` (the strip, as the page mounts it).
- [x] **T3.10** — `unsaved-changes-coverage.test.ts`, `landmarks.test.ts`, `status-interpretation.test.ts` · the rule
  editor and channel form joined the coverage list; the other two pass unchanged.

### Step 4 — Noise controls

*Judgeable:* a check pointed at a URL that alternates fails five times in an hour and the channel receives the
*flapping* message and nothing more; five checks failing together arrive as one message; a warning in quiet hours
arrives as a digest when they end.

- [x] **T4.1** — `scheduler/alerts/incidents.py` · flap guard (FR11, AC12) · proved by `test_alert_noise_live.py`.
- [x] **T4.2** — grouping within 30 s (FR13, AC13), burst cap and summary (FR12, AC14), quiet-hours hold and digest,
  criticals never held (FR15, AC15), muted-channel rows recorded as `dropped` · proved by
  `test_alert_noise_live.py`, `test_alert_outbox_live.py`, `test_quiet_hours.py`. *As built:* shaping lives in
  `outbox.py`'s claim, where every due row for a channel is visible (ADR-013 §4), not a separate `shaping.py`; and
  it applies to channels people read only — PagerDuty and webhooks get one event per notification.
- [x] **T4.3** — renotify, ≥ 30 min, ≤ 5 repeats (FR10) · proved by `test_alert_noise_live.py` (AC11 and FR10).
  *As built:* folded into `sweep_deliveries` rather than its own beat entry — it runs every 15 s with delivery.
- [x] **T4.4** — console: mute controls with who and until when (FR14) on rules and channels; *flapping* and *muted*
  on incidents · proved by `RuleEditor.test.ts`, `AlertsScreen.test.ts`, `ChannelsScreen.test.ts`.

### Step 5 — Service conditions: silence, thresholds, new errors

*Judgeable:* stop the OpenTelemetry Demo's `cart` service and a *went silent* incident opens after the configured
silence, resolving when it restarts; a p95 rule with a low threshold fires on the demo's checkout and not under its
minimum request count; a new exception type thrown by a demo service is announced once.

- [x] **T5.1** — `0029_alert_telemetry_state.py`, `models.py` · `service_last_seen`, `error_groups_seen`,
  `error_group_watermarks` · proved by `test_alerts_migration_live.py` (round trip).
- [x] **T5.2** — `apps/workers/internal/pipeline/usage/postgres.go` · `markServicesSeen` upserts `service_last_seen`
  with `GREATEST` · proved by a new case in `usage/storeprobe_test.go` (an older flush never moves it back).
- [x] **T5.3** — `scheduler/alerts/silence.py` + sweep · Q2's alternative: judged against the clock, with a guard
  that skips the minute when the meter has unread data and has not advanced for 5 min (read from Kafka high
  watermarks against `usage_ingest_offsets`) · proved by `test_alert_service_conditions_live.py` (AC10) and the
  live run below.
- [x] **T5.4** — *As built, differently:* the statements were not moved. `binds_tenant` and `READ_SETTINGS` moved
  to `nexovue_platform.tenant_statements` (the API re-exports them from `data/clickhouse/base.py`), and the
  scheduler's chokepoint is its own `alerts/telemetry.py`: a `TelemetryStore` whose `for_project()` returns the
  only reader, bound to one tenant. Proved by `test_tenant_chokepoint_structure.py` extended to the scheduler, and
  `test_alert_telemetry_live.py` (an unbound statement refused before it is sent).
- [x] **T5.5** — *As built, differently:* the shared definitions are fragments — quantiles, the P95 index,
  `failure_ratio`, the group-id and message-template SQL — in `nexovue_platform.readings`, which the API's
  `rollup.py`, `errorgroups/queries.py` and the scheduler's statements all build from. Proved by the existing
  services and error-group tests unchanged, and `test_alert_telemetry_live.py` asserting the scheduler's
  statement is built from the Errors screen's fragment.
- [x] **T5.6** — scheduler `settings.py`, `pyproject.toml` · `clickhouse_dsn`, `clickhouse-connect` · proved by
  `test_scheduler_settings.py`. The read-only user is **not** done — see *Outcome*.
- [x] **T5.7** — `scheduler/alerts/thresholds.py` + sweep · window ending 2 min ago, fire/resolve waits from
  `alert_evaluations`, *not enough traffic* (FR6), a skipped minute when ClickHouse is down · proved by
  `test_alert_thresholds.py` and `test_alert_service_conditions_live.py` (AC9).
- [x] **T5.8** — `scheduler/alerts/new_errors.py` + sweep · baseline, `INSERT … ON CONFLICT DO NOTHING RETURNING`,
  event incidents; a history past one read is taken in parts · proved by `test_alert_service_conditions_live.py`
  (AC8).
- [x] **T5.9** — `deploy/compose/*.yaml` · `CLICKHOUSE_DSN` for `scheduler-light`, which waits for ClickHouse ·
  proved by `test_compose_dependencies.py`.
- [x] **T5.10** — console: service conditions in the rule editor, *not enough traffic*, the *evaluation behind*
  banner · proved by `RuleEditor.test.ts`, `AlertsScreen.test.ts`. *As built:* the last hour is shown beside the
  threshold as numbers (requests, error rate, p95 per service), not drawn as a chart.

### Step 6 — Proved together

*Judgeable:* the end-to-end suite passes against the stack; the restart test passes; the numbers are in
`status.md`.

- [x] **T6.1** — `tools/e2e/tests/alerts.spec.ts` · a new organization's default rule; a webhook channel connected,
  its secret shown once, send test; a rule to it; a failing check opens an incident shown on the Alerts screen and
  the check's page.
- [x] **T6.2** — AC19 · *as built:* proved by stopping the `scheduler-deliveries` container between decision and
  send in the running stack (delivered once after restart), with the lease behaviour held by
  `test_alert_outbox_live.py` (a lease that ended is claimed again under the same batch id; a worker whose lease
  ended cannot undo the delivery that replaced it). No automated process-kill test.
- [x] **T6.3** — sweep timing at 1, 10 and 100 rules, in `status.md`.
- [x] **T6.4** — docs: `docs/architecture/{system,database,deployment}.md`, `docs/product/feature-catalog.md`,
  `docs/product/setup/alerts/{channels,webhooks}.md` (the webhook payload schema and verification).
- [x] **T6.5** — `/review-diff`, then `/readiness-check` for F21.

## New risks

- **The email-port move touches signup, recovery and profile** — the three places mail matters most. Its tests
  pass unchanged or the move is wrong; nothing about the port's behaviour may change in T1.1.
- **The chokepoint move (T5.4) changes the file the whole ClickHouse tenancy guarantee rests on.** It is moved, not
  rewritten, and its structure test is extended in the same task and observed failing on a deliberately unbound
  statement before it passes.
- **Scheduler SQL is not covered by the Postgres tenancy structure test**, which walks API source. The alert sweeps
  read across tenants by design (every silence rule, every due notification); each per-tenant statement binds
  `org_id` and is reviewed by name in `/review-diff`. A scheduler-side structural test is not in this plan — Q3.
- **Hook failure blocks a state change.** T1.10 makes an alert-write failure roll back the check's state change, by
  design (the two commit together). A defect in alert code would therefore stop check states recording until fixed;
  `record_outcome`'s five retries are not a cure for a bug. T1.10's test covers the rollback; the alert code is kept
  to plain inserts on that path so the surface is small.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| Q1 | **A separate `scheduler-deliveries` worker?** The design put deliveries on their own queue consumed by `scheduler-light`, but that worker consumes every declared queue on the same eight slots, so a slow receiver would still take probe slots. **Assumption:** a separate worker service consuming only `light.deliveries`, with `scheduler-light` excluding it (`-X`). One more container in each compose file. | **Answered 2026-09-19: yes, as assumed** | `design.md`, *Data flow* |
| Q2 | **Silence judged against the meter's watermark, not the clock?** Against the clock, a meter that falls behind makes every service in every organization look silent at once. **Assumption:** silence = `last_seen_at` older than the rule's silence *measured back from the meter's newest `counted_through_received_at`*. Cost: if nothing at all is being ingested anywhere, silence cannot fire — acceptable on a hosted service with many tenants, but on a single-tenant self-hosted deployment whose only service stops, it would not alert. **Alternative:** the clock, with a guard that skips the sweep when the meter itself has not advanced for 5 minutes and alerts us instead. I lean to the alternative; say which. | **Answered 2026-09-19: the alternative (A)** | `design.md`, *Service went silent* |
| Q3 | **A structural tenancy test for scheduler SQL**, like the API's? Not required by the spec or design; the scheduler's existing SQL has lived without one. **Assumption:** not in F21; recorded as a risk. | **Answered 2026-09-19: not in F21, as assumed** | `status.md`, *Risks* |

## Gates

- [x] Specification approval — 2026-09-18, amended 2026-09-19
- [x] Design approval — 2026-09-19
- [x] **Plan approval — before the first edited file** — 2026-09-19
- [x] Implementation — 2026-09-19
- [x] Tests — 2026-09-19
- [x] Review — `/review-diff`, 2026-09-19; findings and fixes in `status.md`
- [x] Design QA — the built screens against the design's *UI structure*, desktop, dark and phone, 2026-09-19
- [x] Production-readiness assessment — 2026-09-19, as one change: no blocker of its own (`status.md`)
- [x] Documentation and status update — 2026-09-19

## Validation plan

- **Per step:** the step's tests, each observed red first; `just check` with counts read; the step's judgeable result
  shown in the running stack.
- **End:** `just check` (counts compared with the 2026-09-18 baseline: Python 3,147, web 1,167); `just e2e` with the
  new spec; the restart test (AC19); the sweep timings; `/review-diff`; `/readiness-check`.
- **Every acceptance criterion is mapped to a named test above** — AC1–AC27. A criterion without one at the end is a
  gap, reported rather than rounded up.

## Rollout and rollback

Pre-deployment, so the real risk is not a rollout. It is **Step 1 shipping and nobody reading a real alert email**:
the words of the message are the feature's first impression and are judged by reading one, not by a test. Step 1
stops for you to read the email it sends before Step 2 starts.

Rollback before deployment is `git revert` plus `alembic downgrade` of `0029` and `0028`; both downgrades are tested
in T1.3 and T5.1.

## Outcome

Built as planned in six steps, 2026-09-19; uncommitted for the founder's review.

- **Gate:** `just check` exit 0 — Python **3,454 passed**, 3 skipped (3,147 at the 2026-09-18 baseline); web
  **1,214** (1,167); browser SDK 120; Go every package `ok`; ruff, mypy, biome, `tsc` clean.
- **End to end:** `tools/e2e/tests/alerts.spec.ts` passes against the stack running the final code.
- **Every acceptance criterion** is mapped to its proof in `status.md`; AC6 is proved by composition and AC19 by
  a live stop of the deliveries worker, each said so.
- **Deviations** are stated in their tasks: shaping in `outbox.py`; renotify inside the delivery sweep; machine
  channels unshaped; the chokepoint and readings as shared fragments rather than moved statements (T5.4, T5.5);
  the queue named `deliveries` (not `light.deliveries`, which would collide with probe-location queues); silence
  judged against the clock with a Kafka-lag meter guard (Q2); the threshold's last hour as numbers, not a chart;
  several tests living in other files than named.
- **Review** found one HIGH and eight MEDIUM defects, all fixed with tests (`status.md`, *Review*).
- **Decisions after review** (founder, 2026-09-19): machines told when an incident closes without recovering
  (spec D8, built); FR11 corrected; the rest kept as built (`status.md`, *Decisions*).
- **Not done:** a read-only ClickHouse user for the scheduler — deferred to hosting (`TODO(hosting)`).
