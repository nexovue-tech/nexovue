# Plan: Anomaly detection (F6)

> **Status:** Approved by the founder, 2026-11-01, as revision 2, with corrections C1 to C26 · In progress.
> The founder asked for the whole plan to be built without stopping at each step.
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
| C4 | The partition count comes from Kafka metadata | Nothing reads metadata. The silence sweep takes its partitions from stored rows (`alerts/silence.py:47-52`) | The scheduler gains a `list_topics` read beside `KafkaWatermarks` (`alerts/sweeps.py:44-60`) (T2b.1) |
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
| C17 | Detection's freshness reuses `alert_evaluation_status` | `record_evaluation` sets `finished_at` even on a skip (`jobs/results.py:25-28, 61`), and the API's Alerts screen reads that table (`alerts/evaluation.py:10,36`) | Detection's freshness is each project job's last completed run (design 2.1, item 2). `alert_evaluation_status` is untouched (T2c.3) |
| C18 | The headline replaces "All systems normal" | #22 already removed it. `headline.ts` reports only what the checks said | The detection status adds its own lines above the checks' lines (T3.5) |
| C19 | Postgres migration 0040 | `0040_a_plan_can_have_a_minimum.py` exists (from #21) | The migration is **0041** |
| C20 | One step can carry both migrations | `test_erasure_inventory.py:80` and `test_deletion_live.py:946` read the migrations, so they fail as soon as a table exists that the inventories don't list | Each table's inventory entries go in the **same task** as its migration |
| C21 | `test_alert_vocabulary.py` is re-pinned to the new migration | It reads seven constants that exist only in `0028_alerts.py` (`:20-46`) | It is restructured to read each constant from the latest migration that defines it, in the same task as `anomaly_finding` (T2c.1) |
| C22 | A refusal holds one partition (C2) | `insertEndpoints` and `insertCounters` send one batch for every partition (`pipeline.go:550-574`) | A refusal holds every partition in that settlement. `stepOverRefusal` returns the refusal to its caller (T2a.3) |
| C23 | `scheduler-detection` is a new role | `scheduler-deliveries` is only `worker --queues deliveries` (`roles.py:19-30`), and D32 removed the new container | `scheduler-light`'s command gains `--queues light,detection`, with a cap on concurrent detection jobs. `roles.py` is unchanged |
| C24 | New tables need only migrations | `projects.timezone NOT NULL` has no default for existing rows. Compare-and-set needs a `detection_state` row. `intake_refusals`, `detection_events` and `baseline_shifts` grow without limit | A server default of `'UTC'`. The row is created when a service is first seen. A prune joins the existing expiry sweep (`EXPIRY_SWEEP_ENTRY`) |
| C25 | `write_secrets` can be called on upgrade | It generates every password at once (`deploy/nexovue:116-121`) | A new `add_missing_secrets` adds only keys that are absent (T2b.3) |
| C26 | The architecture docs need only `current-system-reference.md` | `security-and-tenancy.md`, `system.md`, `database.md` and `observability.md` describe the parts this changes. AGENTS.md's "the ingest path does not write Postgres except workers' usage metering" is already untrue (`receiver/auth/lastused.go`) | All five docs are updated, and AGENTS.md's invariant is corrected (T8.5) |

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
| Services | The worker (trace pipeline), the receiver, the scheduler (`scheduler-light` takes the `detection` queue; no new container, D32) and the API |
| Schema / migrations | **ClickHouse 000035:** `service_stats_1m`, `detection_readings`, `span_derived_progress`. **Postgres 0041:** the eight tables, the two `projects` columns, `notifications.finding_id`, the widened CHECKs |
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

Ordered so the first step ships something you can see (ADR-0019). Each step is built whole, then its checks
run once, and then it is committed as one group. Test files named `test_detection_*` and `test_findings_*`
are all new. Nothing is pushed until the feature is done.

**Every task that creates a table also adds its inventory entries** (C20): erasure for ClickHouse, deletion
for Postgres, and each tenancy-test exemption.

### Step 1 — Measure the demo's real variability, alongside the build

*Judgeable:* `detection-measure.json` from your Mac, and the simulation rerun at the real burst rate.

- [ ] **T1.1** — You run `caffeinate -dimsu just dev` for at least 3 days, then `just detection-measure`.
  Proved by the JSON, read into `tools/detection-sim/RESULTS.md`, *Measured*.
- [ ] **T1.2** — `tools/detection-sim/detect_sim.py`: a `--from-measure` option that runs the sweep at the
  measured rate. It also reports false findings **by severity**, and at **40 services per project** (on-call
  review).
  - Proved by a `RESULTS.md` row.
  - **The gate (D14):** if the real rate breaks the budget, work stops after the step in progress, and the
    result comes to you.
- [ ] **T1.3** — `justfile`: a `detection-sim-check` recipe running the tool's strict mypy, added to `lint-py`.
  Proved by `just lint-py` failing on a type error planted in the tool, then passing once it is removed.

### Step 2a — The worker counts every service and records how far it has written

*Judgeable:* on the dev stack, `SELECT … FROM service_stats_1m` shows one row per service per minute.
`span_derived_progress` shows one row per partition, advancing each minute and holding still when the
worker stops.

- [x] **T2a.1** — The per-service rollup.
  - `derive/services.go` (new): `ServiceStat`, with its request and error counts and 64 fixed log-spaced
    latency buckets, plus the cap of 2,000 service names per organization.
  - `endpoints.go` `observeEndpoint` feeds it. `counters.go` gains the map in `Counters`, `NewCounters`,
    `drain`, `Disown`, `Derived` and `Empty()`.
  - Proved by `derive/services_test.go`:
    - every span is counted;
    - a revoked partition's share leaves;
    - the drain is byte-reproducible;
    - the cap.
- [x] **T2a.2** — The writer and the received-at thread.
  - `derive/servicewriter.go` (new, modelled on `endpointwriter.go`) inserts with a dedup token.
  - `traceassembly/stages.go` and `pipeline.go` carry each partition's last counted `received_at` (C3).
  - Proved by `derive/servicewriter_test.go`, and by
    `traceassembly/settlement_test.go`'s `TestTheSettlementCarriesEachPartitionsLastReceivedAt` (new).
- [x] **T2a.3** — `ingeststate/progress.go` (new) writes `span_derived_progress` beside `ingest.Complete`, for
  the settlement's partitions only (C1).
  - A refusal holds the whole settlement (C22). The hold lasts at most 30 minutes (design 2.1, item 1).
  - `traceassembly/refusal.go` `stepOverRefusal` returns the refusal.
  - Proved by `ingeststate/progress_test.go`:
    - a refusal holds the settlement;
    - the 30-minute bound;
    - a revoked partition is not written;
    - a replay never moves progress backwards.
  - Also by `progress_storeprobe_test.go`, run on your Mac with `go test -tags storeprobe`.
- [x] **T2a.4** — `deploy/clickhouse/migrations/000035_detection.up.sql` and `.down.sql`, with each table's
  reasoning in the header:
  - `service_stats_1m`, with a dedup window and a TTL of the lesser of the derived retention and 35 days;
  - `detection_readings`, `ORDER BY (tenant_id, project_id, service_name, minute)` with a version column;
  - `span_derived_progress`.

  The same task updates:
  - `scheduler/erasure/tables.py` (`DERIVED_TABLES` and `EXCLUDED_TABLES`);
  - `deploy/backup/common.sh` and `verify.sh`;
  - the chokepoint test's reasoned exemption for `span_derived_progress`, tied to `detection.settle`.

  Proved by:
  - `setup/migrations_test.go`;
  - `test_erasure_inventory.py`;
  - `test_tenant_chokepoint_structure.py`, with the exemption planted wrong first, then corrected.

### Step 2b — The scheduler knows which minute is settled, and can write a reading safely

*Judgeable:* on the dev stack, the scheduler's log names the settled minute each minute. With the worker
stopped, the log says *behind*. One test reading row appears in `detection_readings` for a seeded project.

- [x] **T2b.1** — `nexovue_scheduler/detection/settle.py` (new): the settled minute, from
  `span_derived_progress`, the Kafka high watermarks (`alerts/sweeps.py:44-60`) and a new `list_topics` read
  (C4), with the 2-minute allowance. Proved by `test_detection_settle.py`:
  - an idle partition is caught up;
  - unread data with old progress is behind;
  - a missing partition is behind.
- [x] **T2b.2** — The project-bound store, and its tenancy rules (ADR-023).
  - `detection/store.py` (new): project-bound reads with both predicates and lowered per-call limits, plus the
    writer, which is bound by `for_project` and takes `org_id` from the `projects` row.
  - `tenant_statements.py`: the override can only lower limits, and the `project_id` rule.
  - `test_tenant_chokepoint_structure.py`: the `project_id` rule for `detection.*`, and `INSERT INTO` and
    `.insert(` allowed only in `detection.store`.
  - Proved by:
    - the structure test, with three planted violations seen failing first;
    - `packages/py-platform/tests/test_tenant_statements.py` (new): an override that raises a limit is refused;
    - `test_detection_store_live.py`: written as A and read as B, nothing is found. The same service name in
      two tenants survives `OPTIMIZE … FINAL`. The API's account is refused an insert.
- [x] **T2b.3** — The `nexovue_detection_writer` account.
  - `deploy/clickhouse/users.d/detection-writer.xml`: its own profile with read-only settings, a write
    quota, the internal network, and `INSERT ON nexovue.detection_readings` only.
  - It is wired in the eraser's places, with `${…:?}` in the deploy compose.
  - The DSN is given to `scheduler-light` only (D32).
  - `deploy/nexovue` gains `add_missing_secrets`, called by `cmd_upgrade` (C25).
  - Proved by `test_compose_contract.py`, `test_deployment_files.py` and `test_scheduler_settings.py`.
  - Also by `test_install_script.py`: an upgrade adds the missing key and leaves every existing one
    unchanged. This case is new, and is seen failing first.

### Step 2c — One signal, end to end: a slowdown on the demo becomes a finding in the feed

*Judgeable:*
1. Seed 4 weeks of demo history with `just detection-seed`, so it can be judged on day one.
2. Turn on `imageSlowLoad`. Within *time to catch*, the feed shows "*frontend* is N× slower than normal since
   14:02", in the browser's zone until Step 3.
3. Turn it off. The finding closes as *recovered*.

- [x] **T2c.1** — The Postgres migration and the new condition kind.
  - `apps/api/migrations/versions/0041_anomaly_detection.py`: all eight tables, each with `org_id`
    (`intake_refusals` and `detection_events` are install-wide, with exemptions); `projects.timezone` with
    the default `'UTC'`; `detection_sensitivity`; `notifications.finding_id`; and the widened CHECKs.
  - `py-platform/alerts/model.py` gains `anomaly_finding`, in this same task (C21).
  - `test_alert_vocabulary.py` is restructured (C21). `organizations/tables.py` moves `notifications` under
    `projects` and adds the tables, with fixture rows.
  - Proved by:
    - `test_migration_rules.py`;
    - `test_anomaly_detection_migration_live.py` (new);
    - `test_alert_vocabulary.py`, seen failing on the new kind first;
    - `packages/py-platform/tests/test_alert_model.py`;
    - `test_deletion_live.py`.
- [x] **T2c.2** — `detection/method.py` (new), *slower* only, ported from `tools/detection-sim`. Proved by
  `test_detection_method.py` (AC2, AC5, AC6, AC8), and by a parity test against the simulation's function.
- [x] **T2c.3** — The jobs.
  - `detection/baselines.py` (new) is the hourly baseline job.
  - `detection/project_job.py` (new) creates a service's `detection_state` row when it first sees the service,
    judges it, writes the readings, then commits state (compare-and-set) and findings in one transaction. It
    records its own completed run, for freshness (C17).
  - `detection/coordinator.py` (new) is the `sweep:detection` entry in `beat.py`, on the `detection` queue.
    `scheduler-light`'s command gains `--queues light,detection`, with at most 2 detection jobs at once,
    shared fairly between organizations (C23).
  - Proved by:
    - `test_detection_project_job_live.py`: AC8, AC12 (slower), AC14 and AC25, with a killed job;
    - the compare-and-set test, two jobs at once;
    - `test_beat_schedule.py`;
    - `test_compose_contract.py`.
- [x] **T2c.4** — `tools/detection-seed/` (new), behind a `just detection-seed` recipe. It writes 4 weeks of
  `service_stats_1m` straight into the dev ClickHouse for the demo's services, refusing any host but
  localhost.
  - Writing directly is needed because the receiver accepts spans only up to 7 days old
    (`clocktrust.PastSkew`), so `tools/gateseed` cannot reach back 4 weeks.
  - Proved by the seeded service leaving *learning* at once, and by the localhost refusal test.
- [x] **T2c.5** — `apps/api/src/nexovue_api/findings/` (new): `GET /findings`, `/findings/stats` (declared
  first) and `/findings/{id}`.
  - Every route is scope-checked. A read token's view leaves out `acknowledged_by` and `feedback_reason`.
  - Proved by:
    - `test_findings_api_live.py`;
    - `test_read_surface_structure.py`, with `docs/product/setup/api.md`;
    - `test_postgres_tenancy_structure.py`.
- [x] **T2c.6** — `apps/web/src/features/findings/`.
  - `api.ts` reads `GET /findings`. The `TimeWindow` argument, `trend`, `subject` and `impact` map to the API's
    fields.
  - `FindingState` is `open | acknowledged | closed`, with `closed_reason`. `ordering.ts` follows.
  - The card names how a finding closed, and *probable cause* is hidden.
  - Proved by `FindingsView.test.ts`, `ordering.test.ts` and `api.test.ts` (new).

### Step 3 — Every signal, and the states that make silence honest

*Judgeable:* on the demo, with seeded history:
1. `paymentFailure` opens *failing more*, naming the payment endpoint (its real span name, checked first).
2. Stopping one service's traffic opens a critical *losing traffic* finding.
3. A new, unseeded project says it is learning, and when judging starts.

- [x] **T3.1** — `detection/method.py`:
  - *failing more*, with the per-endpoint test, D18's switch and the burst profile capped below critical (D26);
  - an endpoint critical at its own share (D27);
  - *losing traffic*, with its floors, and a stop judged at any traffic (D30).

  Proved by `test_detection_method.py` (AC3, AC4, AC7, AC9 to AC12, AC41's small-service case, AC46), plus
  parity.
- [x] **T3.2** — `detection/baselines.py`: modes, learning in distinct days, the fallback, the pools, the burst
  profile without false-alarm or snoozed minutes, exclusions, and the time zone. Proved by
  `test_detection_baselines.py` (AC1, AC13, AC14, AC17).
- [x] **T3.3** — `detection/project_job.py`:
  - closing that is normal, or below the current sensitivity's minimum (AC30), with its pauses;
  - the 6-hour close, including the mixed case of one signal recovered and one unjudged;
  - severity that only rises, and one finding per service;
  - the project finding, with its organization vote;
  - *service gone*.

  Proved by `test_detection_lifecycle.py` (AC18, AC21, AC30, AC41, D20, and the unique indexes under a race).
- [x] **T3.4** — API: `GET /projects/{id}/detection/status` (with the muted count) and
  `GET /services/{name}/detection`, both scope-checked. Proved by `test_detection_status_api_live.py`:
  - AC26, a stopped job;
  - **a healthy project must never show *not running***;
  - AC42.
- [x] **T3.5** — Web `headline.ts`: the detection lines above the checks' lines (C18), and the error state.
  Proved by `headline.test.ts`.
- [x] **T3.6** — `shared/format/zoned.ts` (new), which names the zone in full where its abbreviation is
  ambiguous, as with IST. Proved by `zoned.test.ts`, including a clock change.

### Step 4 — Notifications

*Judgeable:*
1. A critical finding emails the owners on a new project.
2. A Slack test channel gets *opened*, *joined* (a new signal), *critical* and *resolved*.
3. A webhook gets a version-2 `finding.opened`.
4. A PagerDuty test key gets one incident, with a note when a threshold incident is linked.

- [x] **T4.1** — The outbox's second subject.
  - `alerts/outbox.py`: `Subject`, `_TAKEN`, `_IN_ORDER`, and `Notice` with its subject's kind and id.
  - `delivery.py`: the prefixed `dedup_key`.
  - `messages.py`: the finding titles, joined messages and links.
  - `payloads.py`: webhook version 2 (its fields documented as untrusted), and the PagerDuty note for a linked
    finding (D29).
  - Proved by `test_alert_outbox_live.py`, `test_alert_payloads.py`, `test_alert_messages.py` and
    `test_alert_senders_live.py`.
- [x] **T4.2** — `detection/notify.py` (new):
  - each rule's rows;
  - closing messages to the channels told at opening or escalation;
  - joined signals;
  - findings' own flap guard (C7);
  - mute and snooze;
  - adoption mail to the owners (D22).

  Proved by `test_detection_notify_live.py` (AC19, AC22, AC31, AC44, AC45).
- [x] **T4.3** — `api/alerts/defaults.py`: *Critical problems*. Proved by `test_alert_defaults_live.py`, with
  two rules.
- [x] **T4.4** — `CONFIGURE_DETECTION`, the alerts service's check on every route (security M4), and the five
  copies of the action list (C14). Proved by `test_policy.py`, `test_authorization_coverage.py` and
  `test_alerts_live.py`: a member refused on create, on a change to or from the kind, on enable, on channels,
  on mute and on delete.
- [x] **T4.5** — `alerts/sweeps.py` and `project_job.py` write `finding_incidents`. Proved by
  `test_detection_links_live.py` (AC20).
- [x] **T4.6** — `detection/notify.py`: *detection stopped* and *detection running again* to the owners
  (D24). Proved by `test_detection_stopped_live.py` (AC43).

### Step 5 — The user's controls and the finding page

*Judgeable:* on the demo:
1. Open a finding, acknowledge it, and snooze it as a member.
2. Mark it a false alarm as an admin, and see it close.
3. Confirm a member's false alarm leaves the normal unchanged.
4. Change the sensitivity and time zone in settings.

- [x] **T5.1** — API routes:
  - `POST /findings/{id}/acknowledge`, `/false-alarm` (admin or member, per D28) and `/new-normal`;
  - `POST /services/{name}/snooze`, and `PUT /services/{name}/mute`;
  - `GET` and `PUT /projects/{id}/detection`.

  Each mutating route has its role gate, a `409` on a stale finding, and an audit record with its reason.
  - Proved by:
    - `test_findings_actions_live.py` (AC30 to AC35);
    - `test_authorization_coverage.py`;
    - `test_personal_data_live.py`: the reasons are in the export.
- [x] **T5.2** — `project_job.py` and `baselines.py`:
  - the false-alarm hold;
  - new normal and adoption, with the ratio taken from the readings since the finding opened, and refused
    until there are enough;
  - D22's limits, with the audit as the system actor.

  Proved by `test_detection_shifts_live.py` (AC15, AC16, AC44).
- [x] **T5.3** — `features/findings/FindingScreen.tsx` (new), at `/findings/$findingId`.
  - It goes in `app/router.tsx` and `app/routes/route-manifest.ts`.
  - It uses inline confirmation and `DetectionBadge` (C11). It draws its own band from its readings.
  - Proved by `FindingScreen.test.ts`, which covers every state, a viewer and a member (AC38 to AC40), and by
    `route-manifest.test.ts`.
- [x] **T5.4** — `features/servicedetail/`: the badge, mute, snooze and the finding markers. The bands are
  deferred (D32). Proved by `service-detail.test.ts`.
- [x] **T5.5** — The default time zone.
  - `signup/service.py` and the web signup record the browser's zone (D23).
  - `projects/service.py` uses the actor's zone.
  - Proved by `test_signup.py`, and by `test_projects_live.py` (AC35), each seen failing first.
- [x] **T5.6** — Settings and the rule editor.
  - `features/projects/ProjectSettings.tsx`: a hidden *Detection* section (C10), with sensitivity, the time
    zone and the false-alarm counts.
  - `features/alerts/RuleEditor.tsx` and `alert-words.ts`: the *anomaly finding* condition, admin only.
    *Built in Step 4, because every new project carries* Critical problems.
  - Proved by `DetectionSection.test.ts` (new) and `RuleEditor.test.ts`.

### Step 6 — Safe under nexovue's own failures, and under a hostile tenant

*Judgeable:* on the demo:
1. Stop the worker for 10 minutes. Every service reads *data delayed*, and no finding opens.
2. Stop Kafka for a minute: the same.
3. Cancel requests from a script: nothing is unsettled.
4. Restart the scheduler: no duplicates.

- [x] **T6.1** — `apps/receiver/internal/otlp/`: a `broker_unavailable` reason, recorded only for broker-level
  failures on a live context, at least 5 a minute (design 2.1, item 1). `internal/intake/refusals.go` (new),
  modelled on `auth/lastused.go`. Proved by:
  - `refusals_test.go`;
  - `traffic_test.go`: a cancelled request or a per-record error is not recorded.
- [x] **T6.2** — `detection/settle.py` and `project_job.py`: refusal minutes; the install-wide guard, with
  organization votes, a pipeline signal, the 30-minute bound and the operator alert. Proved by
  `test_detection_settle.py` and `test_detection_failure_live.py` (AC23 to AC25, the guard, the bound, and one
  organization with many projects not tripping it).
- [x] **T6.3** — The prune of `intake_refusals`, `detection_events` and `baseline_shifts`, and findings purged
  at the derived retention (C24, security m4). Proved by `test_detection_prune_live.py` (new).

### Step 7 — Operations

*Judgeable:* *detection stalled* fires in its test, and the runbook walks a stuck partition on the dev stack.

- [ ] **T7.1** — `scheduler/metrics.py`: install-wide gauges (C8). `deploy/monitoring/rules/nexovue.yml`: the
  two alerts. Proved by `deploy/monitoring/tests/nexovue_test.yml` (run by `just lint-deploy` on your Mac)
  and `test_scheduler_metrics_live.py`.
- [ ] **T7.2** — `docs/operations/detection.md` (new), in `dead-letters.md`'s form. Proved by walking it once
  on the dev stack, recorded in `status.md`.

### Step 8 — The test bed, the cost, the docs and the gate

*Judgeable:* the AC36 table for each fault flag, the AC29 measurement against its target, and a green
`just check`.

- [ ] **T8.1** — `tools/fault-bed/` (new). It drives the demo's flags with a reference run and 5 injections
  each, injecting traffic at 40% (D19). Its 3-week warm-up is the seeded history from T2c.4 plus 1 week of
  live demo traffic. Proved by its tests on a recorded run, and by the AC36 table in `status.md`.
  *The tool and its tests are built (2026-11-01). The run, and so the table, waits on the founder's Mac.*
- [ ] **T8.2** — AC29: 200 projects and 2,000 services, with history from `tools/detection-seed` and live load from `tools/gateseed`, with the container's memory
  and the storage recorded. If the target is missed, the design's fallbacks come back to you.
- [ ] **T8.3** — AC37 **waits on the test server's price**. It is recorded as blocked.
- [x] **T8.4** — AC27 (AI off): no AI code exists in Stage 1, so a test asserts that `detection.*` imports no
  AI module. Proved by `test_detection_has_no_ai.py` (new).
- [ ] **T8.5** — Docs (C26):
  - `current-system-reference.md`, `security-and-tenancy.md`, `system.md`, `database.md` and
    `observability.md`;
  - AGENTS.md's ingest-writes invariant;
  - `design.md`, with corrections C1 to C26 written in.
- [ ] **T8.6** — `/review-diff`, `just check` with counts read, and the live, store-probe and chaos suites the
  feature adds, run on your Mac or in CI, with their commands recorded. Every finding is fixed or recorded.

## New risks

- **The rollup gains one row per service-minute** on the hottest path. The worker's CPU is measured in T8.2.
- **Webhook version 2** breaks any version-1 integration. There are no customers yet, and the change log will
  say so.
- **`scheduler-light` now holds the writer's DSN** beside its other secrets (D32). That is accepted for memory,
  and revisited if T8.2 shows probes starved.
- **Seeded history** (T2c.4) is synthetic. A demo pass on it says only that the path works, not that detection
  is accurate. Accuracy comes from T8.1 and T1.2.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | Corrections C1 to C26: approve as written? | Yes, with this plan | `design.md` (T8.5) |
| 2 | Step 1 runs on your Mac alongside the build. Can it start now? | No. Assumed yes | `status.md` |

## Gates

- [x] Specification approval: 2026-10-30, amended through D32
- [x] Design approval: 2026-10-31, revision 2, with revision 2.1's changes
- [x] **Plan approval:** 2026-11-01, revision 2
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screens against the design
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

- **Each step is built whole, then its checks run once** (your rule), then it is committed.
- **Real red-then-green** is seen in these places:
  - T1.3, the planted type error;
  - T2a.3, the refusal hold;
  - T2a.4 and T2b.2, the planted structure violations;
  - T2b.3, the upgrade key;
  - T2c.1, the vocabulary test;
  - T4.3, two rules;
  - T4.4, member refusals;
  - T5.5, the time zone.

  For new pure modules, the first red is an import error, which proves nothing. Their evidence is the parity
  test and the acceptance-criteria cases instead.
- **Live, store-probe and chaos suites need Docker,** so they run on your Mac or in CI. Until one has been seen
  passing there, it is recorded in `status.md` as unverified, and its step is not called done.
- **Step 8:** `just check` in full, the AC36 table and the AC29 measurement.

## Rollout and rollback

nexovue is pre-deployment. Stage 1 ships on its own once Step 8 is done (D32, `phase-2-plan.md`).

The real risk is ADR-0019's: building everything before anything is judged. Steps 2a, 2b and 2c are each
judgeable on the dev stack within a day of being built.

The migrations follow `docs/operations/rollback.md`: a rollback across a schema change uses the restore path.

## Outcome

Filled in when the work completes.
