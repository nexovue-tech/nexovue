# Plan: Anomaly detection (F6)

> **Status:** Awaiting approval
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order. Tasks under
> each step name the file that changes and the test that proves it, read from the code. **The founder approves
> this plan before any file is edited.**

## Preconditions

- **The specification** ([`spec.md`](spec.md)) was approved on 2026-10-30, and amended through D21 and with the
  design on 2026-10-31.
- **The design** ([`design.md`](design.md)) was approved as revision 2 on 2026-10-31. With it,
  [ADR-022](../../decisions/ADR-022-a-finding-is-the-one-record-of-a-detected-problem.md) and
  [ADR-023](../../decisions/ADR-023-the-scheduler-writes-detection-state-to-clickhouse-through-its-own-account.md)
  were accepted.
- **`feature/anomaly-detection` holds `main`** through #22 (merge `d399964`). That includes
  [nexovue-tech/nexovue#20](https://github.com/nexovue-tech/nexovue/pull/20)'s escaping, which the
  finding messages reuse.
- **The design corrections below** are part of approving this plan. Each one is the code disagreeing with the
  approved design, found while writing this plan.

## Current system

Read from the code on 2026-10-31 by five read-only inventories: workers, scheduler, API, deploy and web. Every
claim that changes a task was checked against the source before use.

### Design corrections ⚠

Where the code disagrees with the approved design. Each is corrected in `design.md` in the same commit as its
task, and is listed here so it can be reviewed in one place.

| # | The design says | The code says | Task |
|---|---|---|---|
| C1 | `WriteGuard` refuses writes for a lost partition | It checks offsets only, never ownership (`ingeststate/writeguard.go:100-109`). Handover safety comes from `release`, `queue.Forget` and Kafka's commit fencing (`consume/commit.go:118-140`, `traceassembly/pipeline.go:215-245`) | Progress is written only for partitions in the current settlement's positions (T2.3) |
| C2 | Progress advances after the rollup is acknowledged | A **refused** derived insert is counted and stepped over, and settlement completes anyway (`traceassembly/refusal.go:80-95`) | A refusal holds that partition's progress, so the minute reads *data delayed* (T2.3) |
| C3 | The worker knows the `received_at` of its last counted record | It is computed per message (`traceassembly/stages.go:250`) and kept nowhere. It is not in `absorbed`, `spanStorage` or `consume.Position` | It is carried through the stage into the settlement's positions (T2.2) |
| C4 | The partition count comes from Kafka metadata | Nothing reads metadata. The silence sweep takes its partitions from stored rows (`alerts/silence.py:47-52`) | The scheduler gains a `list_topics` read beside `KafkaWatermarks` (`alerts/sweeps.py:193-210`) (T2.6) |
| C5 | The receiver can record "refused for lack of a store" | `reason="unavailable"` mixes Kafka down with a control-plane authentication failure (`otlp/traffic.go:295-312`, `gates.go:110`). Postgres itself may be the store that is down | A distinct `broker_unavailable` reason, recorded only at the three Kafka-publish refusals (`otlp/signals.go:173,253,266`), through a buffered writer like `auth/lastused.go` (T6.1) |
| C6 | One Postgres migration, additive | Making `notifications.incident_id` nullable changes a column (`models.py:1385`). `anomaly_finding` widens two existing CHECKs (`alert_rules`, `incidents`). `test_alert_vocabulary.py` is pinned to migration 0028 | The migration changes columns and checks on purpose; nexovue is pre-deployment. The vocabulary test follows the latest migration (T2.4, T4.1) |
| C7 | The flap guard is per project and service | F21's guard is keyed per `(rule_id, subject_key)` (`alerts/incidents.py:64-86`) | Findings get their own guard, keyed per project and service, with F21's constants (T4.3) |
| C8 | A gauge and an alert "per project" | `metrics.py:10`: "no label names a tenant" | Gauges are install-wide: the count of projects behind, and the oldest lag. The project is named in logs, never in a label (T7.1) |
| C9 | An upgrade carries the new ClickHouse account | `cmd_upgrade` (`deploy/nexovue:378`) never writes secrets; only install and quickstart do | `cmd_upgrade` writes any secret that is missing (T2.5) |
| C10 | The settings *Detection* section is shown disabled to non-admins | Every gated section in `ProjectSettings.tsx` is hidden, never shown disabled (`:55-71`) | Follow the convention: hidden (T5.6) |
| C11 | The finding page uses shared confirm and badge components | There are none. Confirmation is inline `confirming` state (`ProjectSettings.tsx:270-289`), and the only badge is `alerts/OpenIncidentBadge.tsx` | Inline confirmation, as elsewhere, and a `DetectionBadge` in its own feature (T5.4) |
| C12 | Bands are drawn on the charts | `TrendChart` draws strokes only, with no band or fill (`shared/components/TrendChart.tsx:322-334`). Duration is one chart with p50, p95 and p99 (`servicedetail/trend-series.ts:48-131`). `bucket_seconds` is fetched and never used | `TrendChart` gains an optional band, drawn behind the p95 and failure-share series (T5.3) |
| C13 | Titles name the project's time zone | The web formats in the browser zone only (`shared/format/time.ts:11-26`). There is no named-zone helper | A `shared/format/zoned.ts` helper, used for finding times (T3.6) |
| C14 | `CONFIGURE_DETECTION` is in the migration | Actions are Python only (`auth/permissions.py:16-66`). Five places copy the list: `test_policy.py:22-37`, web `identity.ts:13-27`, `identity.test-util.ts`, `MembersScreen.test.ts:67-87`, and the FR1 table in `team-and-api-access/spec.md` | All five change together (T4.5) |
| C15 | The default time zone is the creating user's | `signup/service.py:114` builds the user with no time zone, and `projects/service.py:99` loads no user | Signup gives UTC. `projects.create` reads the actor's zone (T5.5) |
| C16 | `span_derived_progress` is exempt from the tenant-structure test | The test has no exemption mechanism (`test_tenant_chokepoint_structure.py`) | A named, reasoned allowlist is added to that test (T2.7) |
| C17 | Detection's freshness reuses `alert_evaluation_status` | `record_evaluation` sets `finished_at` even on a skip (`jobs/results.py:98-105`), and the API's Alerts screen reads that table (`alerts/evaluation.py:10,36`) | Detection has its own status, held in each service's `last_judged_minute`. `alert_evaluation_status` is untouched (T2.6) |
| C18 | The headline replaces "All systems normal" | #22 already removed it. `headline.ts` reports only what the checks said | The detection status adds its own lines above the checks' lines (T3.5) |

### What exists and is reused

| What | Where |
|---|---|
| Every inbound span is seen once, before sampling | `derive/endpoints.go:107-133`, reached from `derive/counters.go:250` |
| The endpoint rollup writer, with a dedup token | `derive/endpointwriter.go:17-149`, `insertdedup/token.go:29-60` |
| Settle order: drain, `ingest.Begin`, inserts, `ingest.Complete`, then the Kafka commit | `traceassembly/pipeline.go:489-575`, `consume/consume.go:517-520` |
| KeeperMap progress tables and their loader | `ingeststate/keepermap.go:15-151`; migrations 000010, 000026, 000029 |
| Kafka high watermarks | `alerts/sweeps.py:193-210` |
| Per-item jobs queued from a sweep | `probes/tasks.py:226-275, 404-427`; `jobs/queue.py:110-120` |
| Project-bound, tenant-checked ClickHouse reads | `alerts/telemetry.py:106-149`; `tenant_statements.py:23-73` |
| The outbox, delivery and per-channel payloads | `alerts/outbox.py`, `delivery.py`, `dispatch.py`, `payloads.py`, `messages.py` (with #20's `one_line`) |
| The rule model, default rule and editor | `py-platform/alerts/model.py:24-207`; `api/alerts/defaults.py:36-64`; `web/features/alerts/RuleEditor.tsx` |
| The findings feed, ordering, headline and their tests | `web/features/findings/*` |
| Route, error and state conventions | `api/alerts/router.py`; `errors/exceptions.py`; `docs/design/principles.md` rule 3 |
| Audit records, personal-data export and the member scrub | `audit/service.py:1170`; `profile/personal_data.py:48-80`; `members/service.py:188` |
| The deletion and erasure inventories | `organizations/tables.py:32-114`; `scheduler/erasure/tables.py:22-75` |
| The ClickHouse account pattern | `deploy/clickhouse/users.d/eraser.xml`, and the eleven places it is wired (T2.5) |
| Metrics, alert rules and their tests | `scheduler/metrics.py`; `deploy/monitoring/rules/nexovue.yml`; `deploy/monitoring/tests/nexovue_test.yml` |
| The simulation and the measurement tool | `tools/detection-sim/`, `tools/detection-measure/` |

## Change required

As [`spec.md`](spec.md) (through D21) and [`design.md`](design.md) (revision 2), with the corrections above.

## Impact

| Area | What changes |
|---|---|
| Files | Workers: `derive/`, `traceassembly/`, `ingeststate/`. Receiver: `otlp/`, plus a refusal writer. Scheduler: a new `detection/` package; `alerts/` outbox, delivery, payloads, messages, incidents and sweeps; `beat.py`, `metrics.py`, `erasure/`. API: a new `findings/` module; `alerts/`, `projects/`, `services/`, `auth/`, `organizations/`, `audit/`. Web: `features/findings/`, `servicedetail/`, `services/`, `projects/`, `alerts/`, `shared/`. Deploy: `users.d/`, compose, `deploy/nexovue`, backup and monitoring |
| Services | The worker (trace pipeline), the receiver, the scheduler (a new `scheduler-detection` role) and the API |
| Schema / migrations | **ClickHouse 000035:** `service_stats_1m`, `detection_readings`, `span_derived_progress`. **Postgres 0040:** the eight tables, the two `projects` columns, `notifications.finding_id`, the widened CHECKs |
| UI | The feed, the finding page (new), the service screen, the services list, the settings section and the rule editor |
| Tests | New unit, live and store-probe tests in each step. Changed: tenant chokepoint, authorization coverage, read surface, alert vocabulary, deletion inventory, erasure inventory, compose contract, policy |

## Reuse

Everything in *What exists and is reused*. In particular:

- **Nothing new is built for the outbox's claim, lease or burst cap.** It gains a second subject only.
- **The findings feed is extended, not rewritten.** Its ordering and states stay, with the states renamed.
- **The project job reuses the tenant chokepoint.** It is not a new reader.
- **The repo's own patterns are copied:** the eraser's account pattern for the writer account, and
  `auth/lastused.go`'s buffered writer for the receiver's refusal record.
- **Inline confirmation and `InlineFailure`** are used rather than new components.

## Gaps

What is genuinely new:

- the per-service rollup;
- the per-partition progress, and its `received_at` threading;
- the `detection` queue and its role;
- the detector and the hourly baseline job;
- the writer account and its chokepoint;
- the findings API and its routes;
- `CONFIGURE_DETECTION`;
- the band in `TrendChart`;
- the zoned time formatter;
- the finding page;
- the refusal record;
- the fault injector for the demo run.

## Steps and tasks

Ordered so the first step ships something you can see (ADR-0019): one signal, end to end, on the demo, then
widen. Each step is one commit, made after its checks pass. Nothing is pushed until the feature is done.

### Step 1 — Measure the demo's real variability, alongside the build

*Judgeable:* `detection-measure.json` from your Mac, and a table of real burst rates set against the
simulation's sweep.

This step needs no code, and nothing else waits on it. It runs while Steps 2 to 4 are built.

- [ ] **T1.1** — You run `caffeinate -dimsu just dev` for at least 3 days, then `just detection-measure`.
  Proved by the JSON, which I read into `tools/detection-sim/RESULTS.md`, *Measured*.
- [ ] **T1.2** — `tools/detection-sim/detect_sim.py`: a `--from-measure` option that runs the sweep at the
  measured burst rate. Proved by a `RESULTS.md` row at the real rate, and the budget checked against it (D14).
- [ ] **T1.3** — `justfile`: a `detection-sim-check` recipe running the tool's own strict mypy, added to
  `lint-py`. Proved by `just lint-py` failing on a type error planted in the tool, then passing once it is
  removed.

### Step 2 — One signal, end to end: a slowdown on the demo becomes a finding in the feed

*Judgeable:*
1. With `just dev` running, turn on the demo's `imageSlowLoad` flag.
2. Within *time to catch*, the feed shows "*frontend* is N× slower than normal since 14:02 IST". That holds
   once the service has 3 days of history; before that, it says *learning*.
3. Turn the flag off, and the finding closes as *recovered*.

- [ ] **T2.1** — `apps/workers/internal/pipeline/derive/services.go` (new): a `ServiceStat` per service per
  minute, with its request count, error count and 64 fixed log-spaced latency buckets.
  - `endpoints.go` `observeEndpoint` feeds it. `counters.go` gains the map in `Counters`, `NewCounters`,
    `drain`, `Disown` and `Derived`.
  - Proved by `services_test.go`:
    - the buckets count every span;
    - a revoked partition's share leaves;
    - the drain is byte-reproducible, as in `counters_order_test.go`.
- [ ] **T2.2** — `derive/servicewriter.go` (new, modelled on `endpointwriter.go`): it inserts into
  `service_stats_1m` with a dedup token. `traceassembly/stages.go` and `pipeline.go` carry each partition's
  last counted `received_at` into the settlement (C3).
  - Proved by `servicewriter_test.go`, which checks the token is stable for the same rows.
  - Also by `TestTheSettlementCarriesEachPartitionsLastReceivedAt`.
- [ ] **T2.3** — `ingeststate/progress.go` (new): it writes `span_derived_progress`.
  - The write happens after the derived inserts are acknowledged, beside `ingest.Complete`
    (`pipeline.go:572`).
  - It covers only the partitions in the settlement's positions (C1).
  - A stepped-over refusal holds that partition (C2).
  - Proved by `progress_test.go`:
    - a refused insert does not advance progress;
    - a revoked partition is not written;
    - a replay below `CountFrom` does not move it backwards.
  - Also by `progress_storeprobe_test.go` (the `storeprobe` tag) against ClickHouse.
- [ ] **T2.4** — The migrations.
  - `deploy/clickhouse/migrations/000035_detection.up.sql` and `.down.sql`:
    - `service_stats_1m`, with a dedup window as in 000021, and a TTL of the lesser of the derived retention
      and 35 days;
    - `detection_readings`, a `ReplacingMergeTree` with a version column;
    - `span_derived_progress`, in KeeperMap.

    Each table's reasoning is in the file's header, per `docs/templates/migration.md`.
  - `apps/api/migrations/versions/0040_anomaly_detection.py`:
    - `findings`, `finding_incidents`, `detection_state`, `detection_baselines`, `baseline_shifts`,
      `service_detection_settings`, `intake_refusals` and `detection_events`, each with `org_id` NOT NULL;
    - `projects.timezone` and `detection_sensitivity`;
    - `notifications.finding_id`, its check and its unique keys;
    - the widened `condition_is_known` CHECKs.
  - Proved by:
    - `setup/migrations_test.go` `TestTheRepositorysOwnMigrationsLoad`;
    - `cmd/setup/main_test.go`'s newest-version assertion, updated;
    - `test_migration_rules.py`, and `test_anomaly_detection_migration_live.py` (new), in the pattern of `test_alerts_migration_live.py`;
    - `test_alert_vocabulary.py`, re-pinned to 0040.
- [ ] **T2.5** — The `nexovue_detection_writer` account (ADR-023).
  - `deploy/clickhouse/users.d/detection-writer.xml` grants `INSERT ON nexovue.detection_readings` only.
  - It is wired in the eleven places the eraser is:
    - `compose.infra.yaml`, `compose.infra.deploy.yaml`, `nexovue.yaml` and `docker-compose.yaml`;
    - both `.env.example` files;
    - `deploy/nexovue` `write_secrets`, and `cmd_upgrade` writing missing secrets (C9);
    - `deploy/release/kit.sh`;
    - the scheduler's `settings.py`;
    - `current-system-reference.md`.
  - Proved by `test_compose_contract.py`, `test_deployment_files.py`, `test_install_script.py` and
    `test_scheduler_settings.py`.
  - Also by a new `test_install_script.py` case: an upgrade from a secrets file without the password adds it.
- [ ] **T2.6** — The scheduler's settling and job structure.
  - `nexovue_scheduler/detection/settle.py` (new): the settled minute, from `span_derived_progress`, the Kafka
    high watermarks and `list_topics` (C4), and the 2-minute allowance.
  - `detection/coordinator.py` (new), the `sweep:detection` entry in `beat.py`: it queues one project job per
    active project on the new `detection` queue.
  - `__main__.py` and `apps/api/src/nexovue_api/roles.py` gain a `scheduler-detection` role. The compose files
    gain its container.
  - Proved by:
    - `test_detection_settle.py`: an idle partition is caught up, an unread partition with old progress is
      behind, and a missing partition is behind;
    - `test_beat_schedule.py`: the entry sits on its own queue;
    - `test_compose_contract.py`.
- [ ] **T2.7** — `detection/store.py` (new): project-bound reads through `tenant_statements`, with both
  predicates, per-call limits of 5 s and 256 MiB, and the writer bound by `for_project` (ADR-023).
  `tenant_statements.py` gains the per-call override and the `project_id` rule.
  `test_tenant_chokepoint_structure.py` gains:
  - the `project_id` rule for `detection.*`;
  - `INSERT INTO` and `.insert(` allowed only in `detection.store`;
  - a reasoned allowlist entry for `span_derived_progress` (C16).

  Proved by:
  - the structure test, with three planted violations seen failing first, then removed;
  - `test_detection_store_live.py`: written as project A and read as project B, the row is never found;
  - the API's account refused an insert.
- [ ] **T2.8** — `detection/method.py` (new), *slower* only: the count above L, beta-binomial, leave-one-out,
  the 80% rule, the minimum change and the 200-request floor. It is ported from `tools/detection-sim` and
  pure, with no I/O.
  - Proved by `test_detection_method.py`, synthetic series for AC2, AC5, AC6 and AC8.
  - Also by a parity test: the same series give the same verdicts as the simulation's function, so the
    shipped code is the simulated method.
- [ ] **T2.9** — `detection/baselines.py` (new, the hourly baseline job): level and dispersion pools from
  `service_stats_1m`, written to `detection_baselines`.
  - `detection/project_job.py` (new) judges each service. It writes `detection_readings` and then, in one
    Postgres transaction, `detection_state` (compare-and-set) and `findings`.
  - Proved by:
    - `test_detection_project_job_live.py` covering AC8, AC12 (slower), AC14 and AC25 (a killed leader);
    - the compare-and-set test: two jobs at once judge each minute exactly once.
- [ ] **T2.10** — `apps/api/src/nexovue_api/findings/` (new: `router.py`, `service.py`, `queries.py`,
  `schemas.py`): `GET /findings`, `GET /findings/{id}`, and `GET /findings/stats` declared before `/{id}`, all
  on the read surface.
  - Proved by:
    - `test_findings_api_live.py`, which includes the scope check, as `get_incident`;
    - `test_read_surface_structure.py` `THE_SURFACE`, plus `docs/product/setup/api.md`;
    - `test_postgres_tenancy_structure.py`.
- [ ] **T2.11** — `apps/web/src/features/findings/`:
  - `api.ts`'s `useFindings` reads `GET /findings`;
  - `FindingState` becomes `open | acknowledged | closed`, with `closed_reason`. `ordering.ts` and its tests
    follow;
  - the card shows how a finding closed, never "Resolved";
  - `probable_cause` is hidden until Stage 2.

  Proved by `FindingsView.test.ts`, `ordering.test.ts`, and `api.test.ts` (new).
- [ ] **T2.12** — `design.md` and `docs/architecture/current-system-reference.md`: C1 to C4, C6, C16 and C17
  are written into the design, and the new tables, role and flow into the reference. Proved by the review in
  Step 8.

### Step 3 — Every signal, and the states that make silence honest

*Judgeable:* on the demo:
1. `paymentFailure` opens a *failing more* finding that names `POST /pay`.
2. Stopping the load generator's traffic to one service opens *losing traffic*.
3. A new project's feed says it is learning, and when judging starts.

- [ ] **T3.1** — `detection/method.py`: *failing more* gets the beta-binomial test, the minimum change, the
  per-endpoint test with α ÷ m, and the D18 switch with its burst profile. *Losing traffic* gets the Anscombe
  test and the expected-count floor per sensitivity.
  - Proved by `test_detection_method.py` covering AC3, AC4, AC7, AC9, AC10, AC11 and AC12.
  - The parity test is extended to these signals.
- [ ] **T3.2** — `detection/baselines.py`:
  - the weekly and daily modes, learning counted in distinct days, and the daily fallback;
  - the ±180-minute dispersion pool and the 28-day burst profile;
  - the exclusions, the shadow traffic test, and the project time zone, including the fall-back hour.

  Proved by `test_detection_baselines.py` covering AC1, AC13, AC14 and AC17, and AC35's time-zone change.
- [ ] **T3.3** — `detection/project_job.py`:
  - closing on *normal* (FR13), with pauses for low traffic and delayed data;
  - the 6-hour unjudgeable close (D20);
  - severity that only rises;
  - one finding per service listing every signal (BR2);
  - the project finding with its 3-minute wait (FR15);
  - *service gone* (FR32).

  Proved by `test_detection_lifecycle.py` covering AC18, AC21 and AC41, the D20 close, and the unique indexes
  under a race.
- [ ] **T3.4** — API: `GET /projects/{id}/detection/status` and `GET /services/{name}/detection`, for states,
  bands and markers. Proved by `test_detection_status_api_live.py` covering AC26 and AC42.
- [ ] **T3.5** — Web `features/findings/headline.ts`: the detection status lines (C18). Each takes the line
  above the checks' lines:
  - all learning;
  - some learning;
  - data delayed;
  - detection not running;
  - all judged services normal.

  A failed status read shows the error state. Proved by `headline.test.ts` covering AC26 and AC42.
- [ ] **T3.6** — `shared/format/zoned.ts` (new), which formats an instant in a named IANA zone with the zone's
  short name (C13). Finding titles and times use it. Proved by `zoned.test.ts`, including both sides of a
  clock change.

### Step 4 — Notifications

*Judgeable:*
1. A critical finding emails the owners through *Critical problems* on a new project.
2. A Slack test channel receives *opened*, *critical* and *resolved* for one finding, with the names
   escaped.
3. A webhook receives a version-2 `finding.opened`.

- [ ] **T4.1** — `py-platform/alerts/model.py`: the `anomaly_finding` condition, with services and a minimum
  severity, in `parse_condition`. The migration's CHECKs come from T2.4. Proved by `packages/py-platform/tests/test_alert_model.py`
  and `test_alert_vocabulary.py`.
- [ ] **T4.2** — `alerts/outbox.py`:
  - `Subject`, with `_TAKEN` reading incidents or findings and `_IN_ORDER` matching on the subject's kind and
    id;
  - `Notice` gains `subject_kind` and `subject_id`.

  `delivery.py` gives a `dedup_key` of `incident:<id>` or `finding:<id>`. `messages.py` links to
  `/findings/{id}` and gains the finding titles. `payloads.py` moves the webhook to version 2.
  - Proved by:
    - `test_alert_outbox_live.py`: a finding row is claimed and delivered, and order holds for each subject;
    - `test_alert_payloads.py`: version 2, and both events;
    - `test_alert_messages.py`: the finding wording, escaped;
    - `test_alert_senders_live.py`.
- [ ] **T4.3** — `detection/notify.py` (new). Inside the job's transaction, it writes the `notifications`
  rows for each matching *anomaly finding* rule:
  - a closing message goes only to the channels told at opening;
  - findings get their own flap guard (C7);
  - mute follows BR9 and D8.

  Proved by `test_detection_notify_live.py` covering AC19, AC22 and AC31, and a five-openings flap.
- [ ] **T4.4** — `api/alerts/defaults.py` `provision_project` adds the second default rule, *Critical
  problems*. Proved by `test_alert_defaults_live.py`, whose `len(rules) == 1` becomes 2, and by AC22.
- [ ] **T4.5** — The `CONFIGURE_DETECTION` action.
  - `auth/permissions.py` adds the action, and `auth/policy.py` adds `MayConfigureDetection`.
  - `alerts/service.py` refuses `anomaly_finding` rules without the action. The router passes the identity's
    role.
  - The action is copied in all five places (C14).
  - Proved by:
    - `test_policy.py`;
    - `test_authorization_coverage.py`, with the service check listed in its `SECRET_READS`-style table of
      checks made in the service;
    - `test_alerts_live.py`: a member is refused an anomaly rule (AC34).
- [ ] **T4.6** — `alerts/sweeps.py` (thresholds and silence) and `detection/project_job.py` link incidents
  and findings in `finding_incidents`. Proved by `test_detection_links_live.py` covering AC20.
- [ ] **T4.7** — `organizations/tables.py`: `notifications` moves under `projects`, and the new tables join
  `REMOVED_BY_CASCADE` with fixture rows. Proved by `test_deletion_live.py`.

### Step 5 — The user's controls and the finding page

*Judgeable:* on the demo:
1. Open a finding, acknowledge it, then mark it a false alarm. It closes with one *resolved* message, and does
   not reopen inside *recovery*.
2. As a member, the *New normal* button is absent.
3. In settings, change the sensitivity and time zone.

- [ ] **T5.1** — API: the remaining routes from the design:
  - `POST /findings/{id}/acknowledge`, `/false-alarm` and `/new-normal`;
  - `PUT /services/{name}/detection`;
  - `GET` and `PUT /projects/{id}/detection`.

  Each mutating route has a role gate, a `409` on a stale finding, and an audit record with its reason.
  - Proved by:
    - `test_findings_actions_live.py` covering AC30, AC32, AC33, AC34 and AC35;
    - `test_authorization_coverage.py`;
    - `test_personal_data_live.py`: the reasons appear in the export.
- [ ] **T5.2** — `detection/project_job.py` and `baselines.py`:
  - the false-alarm hold;
  - new normal and adoption, through `baseline_shifts`, with the ratio defined for each signal;
  - adoption's audit, with the system actor, as in `erasure/owed.py`.

  Proved by `test_detection_shifts_live.py` covering AC15 and AC16.
- [ ] **T5.3** — `shared/components/TrendChart.tsx`: an optional band, as a lower and upper series filled
  behind the line, drawn only for ranges of 24 hours or less (C12). Proved by `TrendChart.test.ts` (new): a
  band is drawn, and above 24 hours it is hidden, with a note.
- [ ] **T5.4** — The finding page, `features/findings/FindingScreen.tsx` (new), at `/findings/$findingId`
  in `app/router.tsx`. It has every state in the design, inline confirmation and `DetectionBadge` (C11).
  - Proved by `FindingScreen.test.ts`:
    - loading, not found, partial, closed, muted, the project finding;
    - action failed with a `409`;
    - success;
    - a viewer and a member each seeing the right actions (AC38, AC39, AC40).
  - Also by `route-manifest.test.ts`, with the new route listed.
- [ ] **T5.5** — `features/servicedetail/` gets the badge, the controls and the bands, with markers through
  `deploy-markers`'s pattern. `features/services/ServicesScreen.tsx` gets the *Detection* column.
  `projects/service.py` creates projects with the actor's zone, and signup with UTC (C15). Proved by
  `service-detail.test.ts`, `ServicesView.test.ts` and `test_projects_live.py`.
- [ ] **T5.6** — `features/projects/ProjectSettings.tsx`: the *Detection* section, hidden without the action
  (C10), with validation, unsaved changes, success and the time-zone warning.
  `features/alerts/RuleEditor.tsx` and `alert-words.ts` get the *anomaly finding* condition, behind the
  action. Proved by `DetectionSection.test.ts` (new) and `RuleEditor.test.ts`.

### Step 6 — Safe under nexovue's own failures

*Judgeable:*
1. With the demo running, stop the worker for 10 minutes. Every service reads *data delayed*, and no finding
   opens.
2. Stop Kafka for a minute: the same.
3. Restart the scheduler: no duplicate messages.

- [ ] **T6.1** — `apps/receiver/internal/otlp/`: a `broker_unavailable` reason at the three publish refusals
  (C5). `internal/intake/refusals.go` (new) is a buffered once-a-minute upsert into `intake_refusals`, as in
  `auth/lastused.go`. Proved by `refusals_test.go`, and by `traffic_test.go` for the reason split.
- [ ] **T6.2** — `detection/settle.py` and `project_job.py`: a refusal minute is unsettled. The install-wide
  guard writes `detection_events`. Proved by `test_detection_settle.py`, and by
  `test_detection_failure_live.py` covering AC23, AC24 and AC25, and the guard.
- [ ] **T6.3** — `scheduler/erasure/tables.py`: `service_stats_1m` and `detection_readings` join
  `DERIVED_TABLES`, and `span_derived_progress` joins `EXCLUDED_TABLES`. `deploy/backup/common.sh` and
  `verify.sh` list `span_derived_progress`. Proved by `test_erasure_inventory.py`, `test_erasure_live.py`, and
  `just backup-verify` on the dev stack.

### Step 7 — Operations

*Judgeable:* the Prometheus alert *detection stalled* fires in its test, and the runbook walks a stuck
partition.

- [ ] **T7.1** — `scheduler/metrics.py`: install-wide gauges (C8):
  - the settled-minute age;
  - the number of projects behind, and the oldest lag;
  - failed and overrun jobs;
  - intake events;
  - rows read.

  `deploy/monitoring/rules/nexovue.yml` adds *detection stalled* and *detection failing*. Proved by
  `deploy/monitoring/tests/nexovue_test.yml`, quiet and firing, and `test_scheduler_metrics_live.py`.
- [ ] **T7.2** — `docs/operations/detection.md` (new), in `dead-letters.md`'s form. Proved by walking it once on
  the dev stack, recorded in `status.md`.

### Step 8 — The test bed, the cost measurement, and the gate

*Judgeable:* the AC36 table per fault flag, the AC29 measurement, and a green `just check`.

- [ ] **T8.1** — `tools/fault-bed/` (new). It drives the demo's flagd flags in turn (AC36), with a reference
  run and 5 injections of each flag. It injects *traffic to 50%* at 40% (D19, D21) and scores each injection
  from `findings`. Proved by its own tests on a recorded run, and by the AC36 table in `status.md`.
- [ ] **T8.2** — AC29 and NFR5: a detection pass measured at named counts of projects and services on the dev
  stack, using `tools/gateseed` for volume. Pass time, rows read, memory and storage go into `status.md`. If the
  numbers are over, the design's fallbacks are reopened with you.
- [ ] **T8.3** — AC37, the two-week no-fault run, **waits on the test server's price** (`phase-2-plan.md`,
  Open 2). It is recorded as blocked, not skipped.
- [ ] **T8.4** — `/review-diff`, `just check` with its counts read, and the live, chaos and store-probe suites
  this feature adds. Every finding is fixed, or recorded in `status.md`.

## New risks

- **The rollup grows by one row per service-minute** (T2.1), on the hottest path in the system. The store-probe
  test checks correctness. Its cost on the worker is measured in T8.2.
- **The webhook schema moves to version 2** (T4.2). Any customer integration built on version 1 would break, so
  the change log has to say so. There are no customers yet.
- **A new role and container, `scheduler-detection`** (T2.6), raise the install's memory. That is measured in
  T8.2 against the 16 GB sizing.
- **`cmd_upgrade` writing missing secrets** (C9) changes what an upgrade does to a live install's secrets
  file. Its test proves that it only adds, and never rewrites.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | Corrections C1 to C18: approve as written? | Yes, with this plan | `design.md`, as T2.12 and each task writes them in |
| 2 | Step 1 runs on your Mac while Steps 2 to 4 are built. Can it start now? | No. Assumed yes | `status.md` |
| 3 | Gated settings are **hidden**, following the repo, not shown disabled as the design said (C10) | No. Assumed hidden | `design.md` |

## Gates

- [x] Specification approval: 2026-10-30, amended through D21
- [x] Design approval: 2026-10-31, revision 2
- [ ] **Plan approval — before the first edited file**
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screens against the design
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

- **Every step:**
  - the step's own tests, each regression test seen failing before it passes;
  - `just lint` and the affected suites, with counts read;
  - one commit per step.
- **Live suites** need `just dev`, which this cloud environment cannot run, because it has no Docker. They run
  on your Mac or in CI. A step whose live tests have not been seen passing is not called done.
- **Step 8:** `just check` in full, the store-probe and chaos suites this feature touches, the AC36 table, and
  the AC29 measurement.

## Rollout and rollback

nexovue is pre-deployment. The real risk is the one ADR-0019 names: building all eight steps before the first
one is judged. Step 2 is therefore a thin path you can see on the demo before anything else is widened.

The migrations follow `docs/operations/rollback.md`: a rollback across a schema change uses the restore path.

## Outcome

Filled in when the work completes.
