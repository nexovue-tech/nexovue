# Plan: SLO tracking (F19)

> **Status:** Approved by the founder, 2026-11-02, with D66. Rewritten the same day against the revised spec. Ready to implement once F6 merges.
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order. Tasks under each step name the file
> that changes and the test that proves it, read from the code. **The founder approves this plan before any file is edited.**

## Preconditions

- **The specification** ([`spec.md`](spec.md)) and **design** ([`design.md`](design.md)) are approved. Their two founder decisions were
  answered on 2026-11-02 (13 months on every plan; internal and board use only).
- **P1. F6 is merged.** Then F28, F3, F22, F4 and F5, as the plan's stages order them. They will add migrations and touch `incidents.py` and
  `outbox.py`, so **every line reference below must be read again when this starts.** F19 also re-reads [SigNoz issue 658](https://github.com/SigNoz/signoz/issues/658) at the start of Step 1.
- **P2. The one browser-door change (foundation section 9, shared with F28 and F3) has landed before Step 4:** browser-door spans stay out of the service and endpoint rollups, the cap keeps the busiest services, going over the cap is recorded, and `synthetic.*` readings are accepted only from the prober's door ([`design.md`](design.md), *Forged spans*). It has no record yet. Its timing is settled by foundation item 7 (before Stage 1 reaches a customer), and its owner is F28 (foundation item 22, approved 2026-11-02). It filters by door, not by span kind (spec D63). F19 adds the endpoint rollup, the cap and `synthetic.*` to what F28 requires of it. This plan does not build it. **Step 4 must not start, and F19 must not be released, without it and AC37's end-to-end test in its record.**

- **P3. F3's floor exists before T1.5 ships the SLO queues** (`uncapped_floor`, F3's T1.7; D66). Without it, capped queues are served first and could take probes' slots. If F3's T1.7 has not merged when T1.5 starts, T1.5 builds the floor as F3's design describes.

## Current system

Read from the code on 2026-11-02, branch `feature/anomaly-detection`, with uncommitted work present. Nothing was run. Paths are relative to
`apps/scheduler/src/nexovue_scheduler/` when they begin `alerts/`, `detection/` or `jobs/`.

### Design corrections

Places where writing this plan refined the design. Each is already written into `design.md`.

| # | The earlier text said | The code says | Result |
|---|---|---|---|
| C1 | Research: a default rule "per project" exists to copy from `model.py:225-229` | The existing-project path is a migration insert (`0028_alerts.py:363-372`); `provision_project` covers only new ones (`apps/api/.../alerts/defaults.py:41-71`) | T4.2 does both |
| C2 | Research: `alert_evaluations.subject_key` takes `slo:<id>` without change | True of the table (`models.py:1332`), but `thresholds._positions` strips `service:` for its own rule kinds only (`thresholds.py:195-205`) | F19 has its own read, and does not share `_positions` |
| C3 | Research: reuse `_COUNTED` | It is in the API, and the scheduler cannot import the API (`apps/scheduler/pyproject.toml:14`, foundation section 1) | T2.3 moves it to `nexovue_platform/readings.py` |
| C4 | Research: "Add F19's module to the structural check" | The test's `DETECTION` root and imports walk one package (`test_detection_has_no_ai.py:35`) | T1.10 adds three roots, not just a line |
| C5 | Research R14: "Whether long reads need their own slot limit" | `QUEUE_CAPS` exists for this (`jobs/worker.py:72`), but F3 and F28 also take slots | Three capped queues on `scheduler-light`, under F3's floor (D66, the founder's decision of 2026-11-02; it supersedes D39's role `scheduler-slo`) |
| C6 | Foundation: "`/slos`, `/slos/{id}/budget`" | The spec needs months, history, exclusions, restore, burn and a suggestion too | Added under foundation's rule that a feature may add names |
| C7 | First draft: the job runs `step()`, then "fire or clear" | `step()` returns no action once firing (`thresholds.py:92-95`), and `fire` at the same severity writes nothing (`incidents.py:221-223`) | T4.5 calls `fire` each breached minute, and adds `refresh_detail` |
| C8 | First draft: F6's `active_projects` decides who gets a job | It drops a project silent for 24 hours (`detection/projects.py:22-45`) | T1.5 has its own statement |
| C9 | First draft: plain indexes; downgrades drop tables | `database.md:13-20`, `test_migration_rules.py` | One migration per step; `NOT VALID` then `VALIDATE`; `CONCURRENTLY`; roll forward only |

### What exists and is reused

| What | Where |
|---|---|
| Per-minute service and endpoint rollups | `deploy/clickhouse/migrations/000035`, `000008`; `derive/services.go`, `derive/endpoints.go` |
| Reading buckets from Python | `detection/store.py:160-187`; `detection/method.py:34-37` |
| Tenant-bound scheduler reads | `alerts/telemetry.py:99-149`; `nexovue_platform/tenant_statements.py` |
| Lowered bounds for a job | `tenant_statements.lowered_read_settings`; `detection/store.py:53-57` |
| The pure position function | `alerts/thresholds.py:74-107` |
| Open, escalate, resolve, flap guard, judged alone | `alerts/incidents.py:206-284,315-386,427-443` |
| Settled minute, holds, fair order, project lock | `detection/coordinator.py:87-163`; `detection/projects.py:121,154` |
| Link statements | `alerts/incidents.py:108-118,357-366`; `detection/project_job.py:344-355,1405-1418` |
| The outbox claim, shaping, PagerDuty sharing | `alerts/outbox.py:492-573` |
| The delivery sweep | `alerts/tasks.py:51-68`, on `scheduler-deliveries` |
| Default rules | `apps/api/.../alerts/defaults.py`; `packages/py-platform/.../alerts/model.py:225-229` |
| Rule validation and routes | `apps/api/.../alerts/service.py`; `alerts/router.py` |
| Closing an incident a person changed | `apps/api/.../alerts/queries.py:113-132` |
| Lapsing rows | `jobs/expiry.py:31-44`; `detection/retention.py` |
| A router on the read surface | `apps/api/.../findings/router.py` |
| A spreadsheet-safe cell | `apps/api/.../exports/csv_cells.py` |
| Screens, chart, states | `features/findings/`, `features/alerts/`; `shared/components/` (`DataTable`, `TrendChart`, `EmptyState`, `Skeleton`, `ConfirmPanel`, `InlineFailure`, `Permitted`, `Tabs`, `UnsavedChanges`); `shared/refresh.ts` |
| Fault scoring | `tools/fault-bed/` |
| The measuring tool | `tools/detection-measure/` |

### What is true that may surprise

- **No SLO code exists.** Only a stylesheet in the vendored demo matches.
- **A self-hosted organization is on Free's row:** 7 days raw and 30 days derived.
- **`service_stats_1m` needs no change,** and no ClickHouse migration is in this plan.
- **`tools/detection-seed` writes one table, one fixed backfill** (`seed.py:77-83`). It cannot stop a service, raise failures on command, or seed endpoints or checks. So demos below use the running demo, and T1.11 and T2.6 add what tests need.
- **`tools/detection-measure/scale.py` watches no `metrics_gauge`** (`:106`). T6.2 adds it.
- **`tools/fault-bed` reads `/services` and `/findings` only** (`fault_bed.py:204,223`). T6.3 adds incidents and the delivery log.
- **No test server exists, and none is rented** (foundation item 9, 2026-11-02; *Validation plan*).

## Change required

As [`spec.md`](spec.md) and [`design.md`](design.md). Where this plan and they disagree, that is a finding, not a paragraph.

## Impact

| Area | What changes |
|---|---|
| Files | Platform: new `slo.py`; `alerts/model.py`, `readings.py`. Scheduler: new `slos/`; `alerts/incidents.py`, `outbox.py`, `messages.py`, `telemetry.py`, `tasks.py`; `detection/coordinator.py`, `project_job.py`, `method.py`, `holds.py`; `jobs/registry.py`, `worker.py`, `expiry.py`; `__main__.py`; `metrics.py`. API: new `slos/`; `alerts/`, `auth/`, `audit/`, `organizations/tables.py`, `ratelimit/policy.py`, `data/postgres/models.py`, `main.py`, `members/`, `profile/`, `synthetics/queries.py`, `findings/schemas.py`. Web: new `features/slos/`; `features/alerts/`, `features/findings/`, `features/servicedetail/`, `app/router.tsx`, `app/routes/route-manifest.ts`. Deploy: `deploy/compose/nexovue.yaml` (`scheduler-light`'s comment only); monitoring rules. Tools: `tools/detection-seed/`, `tools/detection-measure/`, `tools/fault-bed/`. Docs: setup, operations, architecture, `docs/self-hosting/sizing.md` |
| Services | **No new role or container:** `scheduler-light` takes three more queues under F3's floor (D66); the API; `scheduler-deliveries` gains two steps. **Worker and receiver: none** (P2 aside) |
| Schema / migrations | One Postgres migration per step that needs one: Steps 1, 3, 4 and 5, with the `VALIDATE` and `CONCURRENTLY` ones apart. **No ClickHouse migration** |
| UI | Four new routes and the *Last month* view; changes to the rule editor, incident, finding and service pages, and the snooze and mute controls |
| Tests | New in each step. Changed: `test_alert_vocabulary.py`, `test_alert_defaults_live.py`, `test_authorization_coverage.py`, `test_ratelimit_coverage.py`, `test_deletion_live.py`, `test_tenant_isolation_live.py`, `test_policy.py`, `test_detection_has_no_ai.py`, `test_detection_coordinator.py`, `test_read_surface_structure.py`, `test_scheduler_postgres_tenancy_structure.py`, `test_migration_rules.py`, `test_compose_contract.py`, `test_mcp_tools.py`, `route-manifest.test.ts`, `RuleEditor.test.ts` |

## Reuse

Everything above. In particular:

- **`step()` is imported and not copied.** Nor is `incidents.fire`, `clear` or `judged_alone`.
- **F21's outbox keeps its claim, lease, batch and retries.** It gains named statements around them.
- **No new chart or confirm component.** Inline warnings use `InlineFailure` and existing tokens. `DataTable` stays for records, and the list is a linked list, as Findings is.
- **The Synthetics screen's rule for which runs count is moved, not rewritten.**
- **The boundary constants move from `detection/method.py` to the platform, so no fourth copy appears.**

## Gaps

What is genuinely new:

- the `nexovue_platform/slo.py` arithmetic and labels;
- three queues on `scheduler-light`, under F3's floor, and three jobs;
- the six tables;
- the `about_*` and `withheld_*` columns;
- `_WITHHOLD_SECOND`, `_RELEASE`, `_DROP_SNOOZED`, and a branch in `_PIN_KEY`;
- `incidents.refresh_detail`, `close_unjudged` and `fire_notice`;
- `CONFIGURE_SLOS`;
- four screens and a view;
- a suggestion route and a CSV route.

## Steps and tasks

Ordered so the first step ships something you can see (ADR-0019). Each step is built whole, its checks run once, and it is offered to the founder to commit as one group. Nothing is pushed without the founder saying so. Test files named `test_slo*` are new.

### Step 1 — A real figure on a real screen, with no alerts

*Judgeable:* On the dev stack with the demo running, you create "checkout availability, 99.9%, 28 days" at `/slos`. Soon after you read "Failures still allowed: N of M". You stop a demo container, and within about 15 minutes it says **No recent requests** instead of looking healthy. You delete the target, and restore it. No message is sent. **This is where to say the wording or the idea is wrong, at the cheapest moment.**

- [x] **T1.1** — `packages/py-platform/src/nexovue_platform/slo.py` (new), `detection/method.py` · kinds, objective limits, whole-day window, FR12's arithmetic, FR10's state and order as one pure function, the composed label, boundaries moved from `method.py`, queue and task names · proved by `apps/api/tests/test_slo_model.py`: AC1's arithmetic, AC51's combinations, AC89, AC110, AC111, and a parity test reading `derive/services.go`'s constants as `tools/detection-seed/test_seed.py` does (AC3's parity half)
- [x] **T1.2** — `apps/api/migrations/versions/<next>_slo_targets.py`, `data/postgres/models.py`, `organizations/tables.py`, `apps/api/tests/test_migration_rules.py` · `slo_targets` with its soft-delete, left-out, sampling and version columns, `slo_status`, `slo_daily_counts`, `slo_target_changes`, with inventory lines (F6's C20). The downgrade refuses, and the revision joins `ROLL_FORWARD_ONLY` · proved by `test_deletion_live.py` (AC46), `test_migration_rules.py`
- [x] **T1.3** — `alerts/telemetry.py`, `slos/store.py` (new) · `_query` gains a `settings` argument; `SLO_SERVICE_DAY_SQL`, availability first; **a first test of `sumForEachMergeIf`** against the dev server, with the design's fallback ready · proved by `apps/scheduler/tests/test_slo_store_live.py`, including that lowered limits are sent, and `test_tenant_chokepoint_structure.py` still passing
- [x] **T1.4** — `slos/counts.py` (new), `detection/holds.py` header · a day's row; quiet and lost minutes; leading, trailing and longest stretches; D37's stated rule in `holds.py` · proved by `test_slo_counts.py`: AC6, AC9, AC10, AC12, AC73, a stretch across midnight, and no good events from missing minutes
- [x] **T1.5** — `slos/job.py`, `slos/__init__.py` (new), `jobs/registry.py:19-30`, `jobs/worker.py:72`, `__main__.py:78-85`, `deploy/compose/nexovue.yaml:351-352` (comment), `detection/coordinator.py:137-163`, `test_scheduler_postgres_tenancy_structure.py` · queues `slo` (cap 2), `slo_counts` (1) and `slo_history` (1) in `scheduler-light`'s default queues and `QUEUE_CAPS`, under F3's floor (D66). **Needs the floor:** if F3's T1.7 has not merged, this task builds `uncapped_floor` and `--uncapped-floor 4` as F3's design describes, and F3 rebases onto it. No new role, compose service or `sizing.md` row; `judge_slos`, with `session_lock` and older-minute guard; `count_slos`, by age; `read_slo_history`, by version; `queue_jobs`' new statement, oldest first, named in `CROSS_TENANT_STATEMENTS`; a failure to queue is logged and does not stop the sweep · proved by `test_slo_job_live.py` (AC8 at 16 minutes, 25 hours, 3, 10 and 27 days through the coordinator, AC64, AC65 at 2 times overload, AC66), `test_detection_coordinator.py` (AC95d), `test_compose_contract.py` and `test_jobs_live.py` (AC68: SLO queues on `scheduler-light` only, within the floor, probes keeping 4 of 8), and AC48
- [x] **T1.6** — `apps/api/src/nexovue_api/auth/permissions.py`, `auth/policy.py`, `test_policy.py`, `apps/web/src/shared/identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts`, and the FR1 table in `docs/features/team-and-api-access/spec.md` · `CONFIGURE_SLOS`, all five copies at once · proved by `test_policy.py` and `test_authorization_coverage.py` (AC32)
- [x] **T1.7** — `apps/api/src/nexovue_api/slos/` (new), `main.py:39,116-`, `audit/service.py`, `test_read_surface_structure.py` and the customer document its list feeds · list, read, create, change, soft delete, restore, for `service_availability` only; `slo_target_changes`, and audit `slo.created`, `slo.target_changed`, `slo.deleted`, `slo.restored`, with ids only; token schemas; F4's ticket refused; `slos/router.py` names no repository, so it stays on the default tier and `TELEMETRY_PATHS` is not touched here · proved by `test_slos_live.py` (AC33, AC34, AC71's first half, AC74, AC97, AC105, AC116), `test_ratelimit_coverage.py`, `test_tenant_isolation_live.py` (AC35)
- [x] **T1.8** — `apps/web/src/features/slos/` (new): `api.ts`, `SlosScreen.tsx`, `SloScreen.tsx`, `SloForm.tsx`, `slo-words.ts`; `app/router.tsx`; `app/routes/route-manifest.ts:18-34` · the linked list, tones, order, headline, banner, deleted line, restore, disable, four routes, 60-second refresh with age worked out in the browser, zone on every time · proved by `route-manifest.test.ts`, `SlosScreen.test.ts` and `SloScreen.test.ts` for each rule 3 state: AC39 and AC40 so far, AC41, AC42, AC43, AC63, AC81, AC82, AC83, AC107, AC108. Also `apps/api/tests/test_slo_words.py` (new): AC13's scan, whole words and ignoring case, over `features/slos`, failing if a named path holds no file. **The step fails without it,** and T3.6, T4.9, T5.7 and T6.6 each add their paths to it
- [x] **T1.9** — Read `apps/workers/internal/pipeline/` for any sampling marker the worker can see on an inbound span · the decision is recorded in `status.md` either way, and this spec's out-of-scope row is corrected by it · proved by the record. Owner: whoever builds Step 1
- [x] **T1.10** — `apps/scheduler/tests/test_detection_has_no_ai.py` · three new roots, `nexovue_scheduler.slos`, `nexovue_platform.slo`, `nexovue_api.slos` (C4), walking subfolders, and a rule that `slos/` modules import no network client. `lifespan.py` types its argument as a small Protocol and drops the `mcp` import, and the walker moves to `nexovue_platform/import_walk.py`, unless F5 or F20 landed first (D55, F20 D38). No exception in the walk · proved by planting an `mcp` import in an API `slos` module and watching it fail once (AC47, AC96's rule)
- [x] **T1.11** — `apps/scheduler/tests/slo_rows.py` (new) · a test helper writing exact rows to `service_stats_1m` and `endpoint_stats_1m`, with day and night traffic · proved by AC1 through `test_slo_store_live.py`. *The `metrics_gauge` rows moved to T2.3 by the founder's decision of 2026-11-08, so the helper is proved by the reader that defines a reading's shape.*

### Step 2 — All four kinds, and a suggestion that needs no thought

*Judgeable:* You create a latency target by typing 500 ms, and the screen says 487.2 ms. You create a target for a demo endpoint and for a check. A service availability target shows the health checks it would leave out. The form proposes a target for each demo service and shows the arithmetic. **You look at what it suggests for each demo service and say whether it looks sensible.** This has never been tried.

- [x] **T2.1** — `slo.py`, `slos/store.py`, new `apps/api/tests/test_slo_system.py` beside `test_system.py` · latency from buckets · proved by `test_slo_store_live.py` with seeded durations, and a live span set through the real worker in `test_slo_system.py` (AC3)
  *2026-11-08: `test_slo_system.py` is written and refuses to run without the whole stack (receiver, Kafka, worker, API); it was not run here, so AC3's live half is not yet seen passing.*
- [x] **T2.2** — `slos/store.py`, `slos/counts.py`, `slo.py` · endpoint counts reading only count columns, and left-out endpoints read by name and subtracted minute by minute · proved by `test_slo_store_live.py`, AC4 and AC80's counts by `test_slos_live.py`, and a capped minute never going negative
- [x] **T2.3** — `packages/py-platform/.../readings.py`, `apps/api/.../synthetics/queries.py:37-60`, `slos/store.py` · move `_COUNTED` and its expressions (C3); extend `apps/scheduler/tests/slo_rows.py` to write exact `metrics_gauge` rows (moved from T1.11, founder 2026-11-08); check counts, at the check's cadence, with expected and missed runs and maintenance runs · proved by the existing synthetics tests unchanged, AC2 and AC11 in `test_slo_store_live.py`, AC67's coverage half
- [x] **T2.4** — `apps/api/.../slos/readings/router.py` (new), `service.py`, `queries.py`, `ratelimit/policy.py:234-249` · `GET /slo-readings/suggestion`, FR7 and FR8, on `CurrentIdentity`; `/slo-readings` joins `TELEMETRY_PATHS` here, with its first route (D59) · proved by `test_ratelimit_coverage.py`, and `test_slo_suggestion.py`: AC75, AC101's latency and check suggestions, a viewer reads, a token is refused
- [x] **T2.5** — `features/slos/SloForm.tsx` · what to watch, kind, generated name, suggestion and arithmetic, boundary preview, definitions, too-fine refusal, sampling question, pre-ticked health checks, suggestion states · proved by `SloForm.test.ts`: AC5, AC79, AC80's form half, AC101, AC106, AC114, and AC81 for the form
- [x] **T2.6** — `tools/detection-seed/seed.py`, `test_seed.py` · endpoint rows, including `/health`-style probes, and check readings: confirmed, overruled, maintenance, `prober_error` · proved by `test_seed.py`
- [ ] **T2.7** — A printed list of what the suggestion says for each demo service · proved by the founder's judgement, written into `status.md`
  *2026-11-08: printed for each demo service on four weeks of `tools/detection-seed` history (`status.md`, Step 2); the running demo was not up. Awaits the founder's judgement, so it is not ticked.*

### Step 3 — The monthly answer

*Judgeable:* You open *Last month* and read "N of M targets met", with what each leaves out. You copy one row into a note and it keeps its label. You download the CSV. You delete a target that missed, and it still counts in last month. **This is the number a buyer asked for, shown before any alert work, so a reporting-only product could stop here.**

- [x] **T3.1** — migration, `data/postgres/models.py`, `organizations/tables.py`, `test_migration_rules.py` · `slo_deleted_months`; the revision joins `ROLL_FORWARD_ONLY` · proved by `test_deletion_live.py`
  *2026-11-08: Alembic **0053**. It holds more than the design's column list (counts, coverage, quiet, lost and excluded time, runs, days, loss known, sampling answer), so a purged month's copy and CSV still carry what FR27b requires; `status.md`, Open decisions.*
- [x] **T3.2** — `slos/job.py`, `slos/store.py` · recount by version, final days, the recount limit, `loss_known`, `changed_at`, the sharp-fall line · proved by `test_slo_job_live.py`: AC7, AC45, AC64's last clause, AC69, AC113
  *2026-11-08: `store.py` needed no change. The sharp-fall line is worked out where months are read, from stored days (`nexovue_platform.slo.sharp_falls`), so AC113 is proved in `test_slo_model.py` and `test_slos_live.py`. AC69's exclusion clause waits for exclusions (T4.7); `status.md`, Open decisions.* *2026-11-09, founder decision 1 (a): AC69's last clause belongs to T4.7, and is built and proved there; T3.2 proves its first two.*
- [x] **T3.3** — `apps/api/.../slos/` · `/months` per target and across targets, `/months.csv` with `spreadsheet_safe`, `/history`, labels per kind · proved by `test_slos_live.py`: AC33 for these routes, AC44, AC78, AC112, AC115
- [x] **T3.4** — `slos/retention.py` (new), `jobs/expiry.py:31-44` · purge after 30 days, writing `slo_deleted_months`; 400-day lapsing · proved by `test_slo_prune_live.py`, on `test_detection_prune_live.py`'s pattern: AC71's second half, AC88's lapsing
- [x] **T3.5** — `members/service.py:188-234`, `profile/personal_data.py` · the free-text scrub; the export gains exclusions and changes · proved by the personal-data tests, extended: AC88
  *2026-11-08: changes only; exclusions and their reasons join the scrub and the export with their table (T4.7).*
- [x] **T3.6** — `features/slos/` · tabs for months, history and definition; the *Last month* view; copy; CSV · proved by `SloScreen.test.ts`, `SlosScreen.test.ts`: AC77, AC78's screens, AC117, and `test_slo_words.py` gains the new tabs (AC13)
  *2026-11-08: not checked in a browser at 375px (AC81), and Step 3's* Judgeable *was not walked on the dev stack.*

### Demand gate

**Alert work starts only after this.** Steps 4 and 5 are the riskiest change in F19, and nothing yet shows SLOs win a purchase.

| | |
|---|---|
| **Who runs it** | The founder, in the one round of five to eight conversations approved as foundation item 8, held before F28's Step 1b, so before F19's Step 1. A mock of the month line is shown |
| **What is shown** | The mock of the month line only: a month figure, never the words Running out or Nothing left (spec D64). Steps 1 to 3 do not exist yet, because the one round comes before Step 1 |
| **Asked** | "When did someone last ask you for a reliability number, who asked, and what did you send them?" "Would this change a purchase?" "We already get an uptime figure from our monitor: is that enough?" "Would you need to define targets in code?" |
| **The bar (spec D62)** | At least 2 people describe a real time in the last 12 months when someone asked them for a reliability number, name who asked (role is enough), and say what they sent was a monitor's uptime figure that did not answer it, or a number built by hand. Opinions ("that would be nice") do not count |
| **Also asked, at the end** | Whether they will do a 15-minute screen test later. The first yes is AC38's tester; the mock alone does not disqualify them (spec D64) |
| **If it passes** | Steps 4 to 6 |
| **If it fails** | Stop. Release Steps 1 to 3 as a report, after P2, with T6.6, T6.8 and T6.9, or leave it unreleased. The founder chooses |
| **If half or more name a customer as the asker** | Legal advice before the month's wording is fixed (founder decision 2) |
| **Also read at the gate** | [SigNoz issue 658](https://github.com/SigNoz/signoz/issues/658). Read 2026-11-02: still open, last activity an outside offer of a design proposal on 2026-10-23. If SigNoz has shipped, F19's rival-gap claim is gone, and the founder should know before deciding |

The result is written into `status.md`, whichever way it goes.

### Step 4 — Burn alerts, on their own channel

*Judgeable:* On the demo, you turn on the `paymentFailure` flag with a target on `payment`. An email arrives: "using its allowed failures 14 times faster than its target allows", with how long what is left lasts. You turn it off, and about 10 minutes later it resolves. A thin service stays silent. You stop `scheduler-light`, and a message says targets are not being judged.

**Needs P2, and the gate.** *2026-11-08: P2 is on `feature/phase-2` with AC37's end-to-end test in its record (`docs/changes/browser-door/plan.md`, T1.3). The gate is no longer a build gate (foundation item 27): Step 4 was built with the buyer round's result still to be read.*

- [x] **T4.1** — `packages/py-platform/.../alerts/model.py:24-31,234-262`, migrations, `data/postgres/models.py`, `test_alert_vocabulary.py`, `apps/api/.../alerts/service.py:222,236-246,325-329,380`, `detection/notify.py:451` · `slo_burn`, `SloBurnCondition`, `parse_condition`, frozen `_CONDITION_KINDS`; CHECKs widened `NOT VALID`, then a `VALIDATE` migration; `incidents.about_*`; `slo_exclusions`. `coverage_of_service` skips the kind; `_covers_subject` reads `slo:`; each consumer reviewed. The revision joins `ROLL_FORWARD_ONLY` · proved by `test_alert_vocabulary.py`, `test_migration_rules.py`, and `test_alerts_live.py`: the coverage route in a project holding the rule (part of AC70)
  *2026-11-08: Alembic **0058** (kind, `about_*`, `slo_exclusions`, the default rule's backfill) and **0059** (`VALIDATE`); ADR-034 filed. `slo_exclusions` also holds `counted_at`, the recount queue; the founder accepted it on 2026-11-09 (decision 5), and `design.md`'s *Data* lists it.*
- [x] **T4.2** — `apps/api/.../alerts/defaults.py:41-71`, the migration · the default rule, for new and existing projects (C1) · proved by `test_alert_defaults_live.py`: AC76
  *2026-11-08: the backfill is 0058's own `BACKFILL_SLO_RULE_SQL`, run by the test. AC76's "No alerts will be sent" is held on the target's read (`test_slos_live.py`); the form's line is T4.12's.*
- [x] **T4.3** — `slos/burn.py` (new) · tiers, floors, the skip when half are failing, cap, severity, how long what is left lasts · proved by `test_slo_burn.py`: AC14, AC15, AC16, AC17's logic, AC92, AC93's logic, AC100, AC103
- [x] **T4.4** — `slos/burn.py` · the six windows summed from each source's per-minute read of the last 24 hours (`slos/store.py`, one statement per source), with a target's excluded minutes left out and nexovue's lost minutes counted against coverage; no `SLO_BURN_*_SQL` · proved by `test_slo_burn.py` and `test_slo_job_live.py`, the minutes read by `test_slo_store_live.py` against `slo_rows.py`
  *2026-11-08: not built as first written (`SLO_BURN_*_SQL`, six windows in one statement per source). The judging already reads each source's last 24 hours minute by minute, one statement per source (design, *What each job does*), so the six windows are summed from those minutes in `slos/burn.py` with no second statement.* *2026-11-09: the founder kept it as built (decision 4); the task's wording above is amended to match. T6.2 measures the read.*
- [x] **T4.5** — `slos/job.py`, `alerts/incidents.py` · position, `step()`, `fire` each breached minute, `refresh_detail`, `clear`, `close_unjudged`; the composed label; `slo_status` columns · proved by `test_slo_job_live.py`: AC17, AC18, AC19 through the job, AC20, AC21, AC23, AC93, AC94's closing, AC104's figures, AC51 with live rows, AC62
  *2026-11-08: the per-rule judgment is `slos/alerting.py` and the per-target burn `slos/burns.py` (new files beside `job.py`). The API reads Running out from `slo_status` (AC51 with live rows in `test_slos_live.py`).*
- [x] **T4.6** — `slos/notices.py` (new), `alerts/incidents.py` · `fire_notice`; exhausted warning, with the union of channels, `exhausted_warned_at` and the 5% re-arm · proved by `test_slo_job_live.py`: AC22 with two rules and an open burn
- [x] **T4.7** — `apps/api/.../slos/`, `slos/job.py`, `slos/notices.py`, `audit/service.py` · exclusions, recount with them, notices, `slo.excluded`, `slo.exclusion_removed`, ids only · proved by `test_slos_live.py`: AC10, AC34, AC60, AC102, and AC69's last clause (with `SloScreen.test.ts`)
  *2026-11-08: the routes are in `slos/exclusions.py`; the recount and the notice are the scheduler's (`job.count_slos`, `notices.exclusion_notices`); the scrub, export and 400-day lapse of reasons came with them (T3.5's deferral).* *2026-11-09, founder decision 1 (a): AC69's last clause is T4.7's. A month an exclusion touched, removed once its days were past the recount limit, carries it (`removed_not_recounted` on `GET /slos/{id}/months` and `GET /slos/months`, `slos/months.py`), and the month says "The exclusion from 3 Oct (2 h) was removed, figure not recounted: …" on screen and in the copied text (`month-words.ts`).*
- [x] **T4.8** — `slos/notices.py`, `alerts/tasks.py:51-68` and a migration of its own · `report_blind` in `sweep_deliveries`, `unjudged_since`, `uq_incidents_blind_open` built `CONCURRENTLY`, its own open and resolve path by `(org_id, project_id, subject_key)` with a null `rule_id` and fixed name (D58) · proved by `test_slo_blind_live.py`: AC91's logic, two sweeps open one row, deleting two rules with open burns on one target still succeeds
  *2026-11-08: Alembic **0060**.*
- [x] **T4.9** — `alerts/messages.py:134-193` · `_said`'s new case and wording, per kind · proved by `test_alert_messages.py`: AC36 for messages, AC89; `test_slo_words.py` gains `slo_burn` wording (AC13)
  *2026-11-08: the words are `alerts/slo_words.py`, which `test_slo_words.py` scans.*
- [x] **T4.10** — `apps/api/.../alerts/queries.py`, `alerts/service.py`, `slos/service.py` · a rule narrowed, a target disabled, deleted or re-pointed, and a check deleted, each close the incident and remove the position · proved by `test_slo_closing_live.py`, including `alert_evaluations` rows: AC70, AC71's closing
  *2026-11-08: a deleted check finds its burns by `detail.check_id`, since `about_check_id` is written only from T5.2.* *2026-11-09: the founder agreed (decision 6); T5.2 switches it to `about_check_id`.*
- [x] **T4.11** — `apps/api/.../alerts/service.py`, `features/alerts/RuleEditor.tsx`, `alert-words.ts`, `features/alerts/IncidentScreen.tsx` · validating `slo_ids`; the new kind and wording; the incident page's facts, links, zone and width · proved by `test_alerts_live.py`, `RuleEditor.test.ts`, `IncidentScreen.test.ts`: AC85, AC104's screen, AC108
  *2026-11-08: built: `slo_ids` checked at write; the rule editor's kind, words and target choice; the incident page's current figures, "As of", joined findings, last deploy ("No deploy recorded" apart from "Deploys are not connected"), the service or check page, the service's errors from the burn's start, the target or "since been deleted", every time in the project's zone with its date when not today, and no width cap (AC85, AC108). The finding line shows once Step 5 links burns to findings (T5.2).*
- [x] **T4.12** — `features/slos/`, `apps/api/.../slos/` `slos/readings/` · who is told, channels on the form, the fixed name of the rule the form writes (D57), chips, `/slo-readings/{id}/burn`, the chart · proved by `SloForm.test.ts`, `SloScreen.test.ts`: AC84, AC109's display, and AC39 for these states; `apps/api/tests/test_slo_readings_live.py` (new): AC109's equality
  *2026-11-08: built: who is told (FR25a's three cases, AC84) on the target page and the form, the list's chips, channels on the form writing this target's rule with D57's fixed name (a second, criticals only, with PagerDuty pre-ticked), `GET /slo-readings/{id}/burn` and the chart with its description; `test_slo_readings_live.py` holds AC109.*
- [x] **T4.13** — `apps/api/tests/test_slo_ai_paths.py` (new), `test_mcp_tools.py` · a payload suite in the gate: hostile target name, description, reason, service, endpoint and **rule name**, and that a rule written by the form holds no target name, through MCP's `list_incidents`, `get_finding`, `list_findings`, F4's `get_incident` and F3's linked-incident evidence; AI off and on give identical rows · proved by itself: AC72, AC96. **The step fails without it**
  *2026-11-08: F4's `get_incident` and F3's evidence do not exist on the branch, and no AI switch exists to turn off, so the suite holds MCP's tools, the no-assistant-reads-`/slos` rule, and a determinism check (AC96); the form-written rule's clause waits for T4.12.*

### Step 5 — What a burn may withhold

*Judgeable:* You break a service with a target and a finding watching it. Your inbox gets **one** message about it. The delivery log shows the other, marked "not sent", with why. PagerDuty shows one alert. You close the finding, the burn still holds, and you get one message saying so. You try the same in the other order.

- [x] **T5.1** — migration, a separate `CONCURRENTLY` migration, `data/postgres/models.py`, `test_migration_rules.py` · `notifications.withheld_*`, `released_at`, and the partial index · proved by `test_migration_rules.py`
  *2026-11-09: Alembic **0063** (columns, `withheld_why` checked `NOT VALID`) and **0064** (the index `CONCURRENTLY`, and the check validated); ADR-038. `test_alert_vocabulary.py` holds the frozen `withheld_why` words.*
- [x] **T5.2** — `alerts/incidents.py:93-104,206-214,357-366`, `detection/project_job.py:344-355`, `slos/job.py` · `Subject`'s about columns; `_open`'s link; `_LINK_INCIDENTS` matches a burn's `about_key`; a deleted check finds its burns by `incidents.about_check_id`, no longer by `detail.check_id` (`alerts/queries.py`, T4.10's closing; founder decision 6, 2026-11-09) · proved by `test_alert_noise_live.py` and `test_detection_links_live.py`, extended for both orders, and `test_slo_closing_live.py`'s deleted check
  *2026-11-09: both orders are `test_detection_links_live.py`'s (the burn opening second through `_open`, the finding second through `_LINK_INCIDENTS`), and the job writing the columns is `test_slo_job_live.py`'s; `test_alert_noise_live.py` runs unchanged, as nothing in it concerns a burn's link. `slos/alerting.burn_subject` composes a burn's subject.*
- [x] **T5.3** — `alerts/outbox.py:515-518` · `_SAME_NEWS`, `_TOLD_AND_OPEN`, `_WITHHOLD_SECOND`, for channels people read · proved by `test_slo_one_message_live.py`: AC24, AC25, AC26, AC30's channel half, AC31, AC53 by the real `judge_slos` through a silence, with F19's save leaving `evaluated_at` alone (D56), AC54, AC55, AC56, AC57, AC58, AC99
  *2026-11-09: AC58 has nothing to hold yet: F3 writes no `explained` notification on the branch (`status.md`, Open decisions). The rule also drops an ending of news that never reached the channel (FR24), `_WITHHOLD_UNTOLD_ENDING`.*
- [x] **T5.4** — `alerts/outbox.py`, `alerts/tasks.py:51-68` · `_RELEASE`, called from the claim and from the sweep · proved by `test_slo_one_message_live.py`: AC52, AC57's release, a person closing a finding
  *2026-11-09: `outbox.release` per channel, and `release_everywhere` from the sweep. A subject whose news reached the channel since (an escalation) gets no second message; its held rows are marked released all the same.*
- [x] **T5.5** — `alerts/outbox.py` · `_DROP_SNOOZED`, reading `service_detection_settings`, and `_RELEASE`'s call, on people's channels and PagerDuty: outside the `READ_BY_PEOPLE` branch, after the channel's mute, before `_shape` and `_PIN_KEY` · proved by `test_slo_one_message_live.py`: AC59. `alerts/incidents.py` gains no `silenced` keyword
- [x] **T5.6** — `alerts/outbox.py:224-259` · `_PIN_KEY`'s branch and severity test · proved by `test_slo_one_message_live.py`: AC27, AC28, AC30's PagerDuty half, AC94's alert
  *2026-11-09: built as the design gives it, AC27's cases held. A burn already noted on a lower-severity alert that later escalates stays a note there, because a subject keeps its first key (`status.md`, Open decisions).*
- [x] **T5.7** — `features/findings/FindingScreen.tsx`, `finding-words.ts`, `apps/api/.../findings/schemas.py:105-112`, `features/servicedetail/`, the service's snooze and mute controls · linked burn line with current figures; targets line; control wording · proved by `FindingScreen.test.ts`, `features/servicedetail/slo-line.test.ts` (new): AC85's finding half; `test_slo_words.py` gains those lines (AC13)
  *2026-11-09: the figures come with `LinkedIncident.burn`, from the incident the job keeps current, not from `/slos/{id}` in the browser as the design wrote (`status.md`, Open decisions). The controls' words are `shared/detection/quiet.ts`, proved by `quiet.test.ts` (new). Not checked in a browser at 375px.*
- [x] **T5.8** — Regression · no outbox or notify test may change, and the suite runs again beside an open burn · proved by `test_alert_outbox_live.py`, `test_detection_notify_live.py` passing **unedited** (AC29), and `test_slo_regression_live.py`: AC95's (a), (b), (c), (e)
- [x] **T5.9** — `docs/product/setup/alerts/webhooks.md`, `channels.md` · what a burn sends, and that a person's channel may withhold · reviewed
  *2026-11-09: written and self-reviewed against the code; the founder's read is at commit.*

### Step 6 — Proving it: cost, demo faults, operations, docs and claims

*Judgeable:* You read `status.md` and see measured cost at the target scale, the fault run's result labelled as demo, a readiness report, and a claims sheet in which each claim names its proof. Nothing here should surprise you.

- [ ] **T6.1** — `metrics.py`, `deploy/monitoring/rules/nexovue.yml`, `deploy/monitoring/tests/nexovue_test.yml` · metrics and the alert · proved by the monitoring tests
- [ ] **T6.2** — `tools/detection-measure/scale.py` · adds `metrics_gauge`, check targets, target creation, and each statement's time and memory; measures 200 projects and 2,000 services, read times, job times, slot use, Postgres writes, claim time with and without the statement at AC50's three counts of open burns, and what buckets on named endpoints would cost, recorded for a later request (spec D65) · proved by AC50's, AC86's and AC90's numbers in `status.md`. **If they fail, the design's order applies, and comes back to the founder if it is more than that**
- [ ] **T6.3** — `tools/fault-bed/` · a target-aware score, reading incidents and the delivery log through the API, and development alert mail switched on so one email can be seen (`docker-compose.yaml:423,539-545`) · proved by AC49, labelled as a demo. Reported number on the no-fault run, never a fixed zero
- [ ] **T6.4** — Failure drill · stop `scheduler-light`, stop ClickHouse, stop the prober and the metrics worker · proved by AC40, AC48, AC67, AC91
- [ ] **T6.5** — Re-run of P2's end-to-end test, `test_slo_system.py`, and the cap's record · proved by AC37 and AC87. Hard exit from this step
- [ ] **T6.6** — `docs/product/setup/` (a new page, with what targets do not measure, what leaves the server, default routing, migrating, an outside heartbeat), `docs/operations/`, `current-system-reference.md`, `database.md`, `security-and-tenancy.md`, `observability.md`, `feature-catalog.md` · reviewed against the must-not list; `test_slo_words.py` gains the new page, and must find "SLO" and "error budget" there (AC13)
- [ ] **T6.7** — `/review-diff`, `/readiness-check`, then design QA against `design.md`, including a named check that no `slos/` module reaches a provider by plain HTTP
- [ ] **T6.8** — Claims table in `status.md`. Owner: the founder or whoever writes launch text · re-read each rival page, with date and quote: Datadog, Honeycomb, Grafana including its self-managed status, New Relic, Coroot, OpenObserve, Elastic, Better Stack, how each groups alerts, SigNoz issue 658 · proved by a dated row per fact. Facts not re-read may not be used
- [ ] **T6.9** — Launch text, release notes and the docs page, against *Must not be claimed*. Owner: as T6.8 · proved by a reviewed list, including "one problem, one message", "no extra alert" and "13 months"
- [ ] **T6.10** — The delivery log's sent and withheld rows per target, and the spec's register of guesses · **needs real traffic, so it runs once a customer exists, and does not block release.** Owner: the founder names one when the first customer exists · proved by a written reading against each guess, and whether any withheld message was needed
- [ ] **T6.11** — AC38's walkthrough on the finished form, with the outside tester found in the conversation round (spec D64), timed by someone other than the author · proved by the time and what the tester said **Running out** means, in `status.md`. No tester: "not proved"
- [ ] **T6.12** — Launch readiness. Owner: the founder signs · one claims sheet (claim, code or test, rival source, read date); a short release note, only after tests pass; a demo script on seeded data, labelled as such, using the dev stack and not data in a prospect's install; a trial script saying what days 1 to 14 show (Building history until a quarter of the window, and add a silence rule or check before stopping a service); a P2-landed check

## Every requirement has a task

A plan check: any requirement or acceptance criterion missing here fails the gate.

### Functional requirements

| Requirement | Tasks |
|---|---|
| FR1 | T1.3, T2.1, T2.2, T2.3 |
| FR2, FR3, FR4, FR5 | T1.1, T2.1, T2.5 |
| FR6 | T1.7 |
| FR7, FR8 | T2.4, T2.5 |
| FR9, FR11, FR12, FR13 | T1.1, T1.4, T1.8 |
| FR10, FR10a | T1.1, T1.8, T4.5 |
| FR13a | T6.5 |
| FR13b | T4.8, T6.4 |
| FR14, FR15, FR16, FR17 | T4.3, T4.5 |
| FR18 | T4.5 |
| FR19 | T4.6 |
| FR20 | T5.2 |
| FR21, FR22, FR24 | T5.3 |
| FR23 | T5.4 |
| FR24a | T5.6 |
| FR25, FR25a | T4.1, T4.2, T4.12, T2.5 |
| FR26 | T5.5, T5.7 |
| FR27, FR27a, FR27b, FR27c | T3.2, T3.3, T3.6 |
| FR28 | T3.2 |
| FR29 | T4.7 |
| FR30 | T1.7, T3.3 |
| FR31 | T1.7, T3.4 |
| FR32 | T1.8, T4.10 |
| FR33 | T1.10, T4.5, T4.13 |
| FR34 | T2.2, T2.5 |

### Acceptance criteria

| AC | Task | Test |
|---|---|---|
| AC1 | T1.1, T1.11 | `test_slo_model.py`, `test_slo_store_live.py` |
| AC2 | T1.11, T2.3 | `test_slo_store_live.py` |
| AC3 | T1.1, T2.1 | `test_slo_model.py`, `test_slo_store_live.py`, `test_slo_system.py` |
| AC4 | T2.2 | `test_slos_live.py` |
| AC5 | T2.5 | `SloForm.test.ts` |
| AC6 | T1.4 | `test_slo_counts.py` |
| AC7 | T3.2 | `test_slo_job_live.py` |
| AC8 | T1.5, T2.3 | `test_slo_job_live.py` |
| AC9, AC10 | T1.4, T4.7 | `test_slo_counts.py`, `test_slos_live.py` |
| AC11 | T2.3 | `test_slo_store_live.py` |
| AC12 | T1.4 | `test_slo_counts.py` |
| AC13 | T1.8, T3.6, T4.9, T5.7, T6.6 | `apps/api/tests/test_slo_words.py` (new in T1.8; each later task adds its paths) |
| AC14, AC15, AC16 | T4.3 | `test_slo_burn.py` |
| AC17 | T4.3, T4.5 | `test_slo_burn.py`, `test_slo_job_live.py` |
| AC18, AC19, AC20, AC21, AC23 | T4.5 | `test_slo_job_live.py` |
| AC22 | T4.6 | `test_slo_job_live.py` |
| AC24, AC25, AC26, AC31 | T5.3 | `test_slo_one_message_live.py` |
| AC27, AC28 | T5.6 | `test_slo_one_message_live.py` |
| AC29 | T5.8 | `test_alert_outbox_live.py`, `test_detection_notify_live.py`, unedited |
| AC30 | T5.3, T5.6 | `test_slo_one_message_live.py` |
| AC32 | T1.6, T1.7, T2.4 | `test_authorization_coverage.py`, `test_slos_live.py`, `test_slo_suggestion.py` |
| AC33 | T1.7, T3.3 | `test_slos_live.py`, `test_read_surface_structure.py` |
| AC34 | T1.7, T4.7 | `test_slos_live.py` |
| AC35 | T1.7 | `test_tenant_isolation_live.py` |
| AC36 | T4.9 | `test_alert_messages.py` |
| AC37 | P2's record, T6.5 | P2's end-to-end test |
| AC38 | T6.11 | A timed walkthrough |
| AC39 | T1.8, T3.6, T4.12 | `SloScreen.test.ts` |
| AC40 | T1.8, T6.4 | `SlosScreen.test.ts`, the drill |
| AC41, AC42, AC43 | T1.8 | `SlosScreen.test.ts`, `SloScreen.test.ts` |
| AC44, AC112, AC115 | T3.3 | `test_slos_live.py` |
| AC45, AC69, AC113 | T3.2; AC69's last clause T4.7 (2026-11-09) | `test_slo_job_live.py`; AC69's last clause `test_slos_live.py`, `SloScreen.test.ts` |
| AC46 | T1.2, T3.1 | `test_deletion_live.py` |
| AC47 | T1.10 | `test_detection_has_no_ai.py` |
| AC48 | T1.5, T6.4 | `test_slo_job_live.py`, the drill |
| AC49 | T6.3 | The fault bed |
| AC50 | T6.2 | The scale tool, in `status.md` |
| AC51 | T1.1, T4.5 | `test_slo_model.py`, `test_slo_job_live.py` |
| AC52 | T5.4 | `test_slo_one_message_live.py` |
| AC53, AC54, AC55, AC56, AC58 | T5.3 | `test_slo_one_message_live.py` |
| AC57 | T5.3, T5.4 | `test_slo_one_message_live.py` |
| AC59 | T5.5 | `test_slo_one_message_live.py` |
| AC60 | T4.7 | `test_slos_live.py` |
| AC62 | T4.5, T1.8 | `test_slo_job_live.py`, `SloScreen.test.ts` |
| AC63 | T1.8 | `SlosScreen.test.ts` |
| AC64 | T1.5, T3.2 | `test_slo_job_live.py` |
| AC65, AC66 | T1.5 | `test_slo_job_live.py` |
| AC67 | T2.3, T6.4 | `test_slo_store_live.py`, the drill |
| AC68 | T1.5 | `test_compose_contract.py` |
| AC70 | T4.1, T4.10 | `test_alerts_live.py`, `test_slo_closing_live.py` |
| AC71 | T1.7, T3.4, T4.10 | `test_slos_live.py`, `test_slo_prune_live.py`, `test_slo_closing_live.py` |
| AC72 | T4.13 | `test_slo_ai_paths.py` |
| AC73 | T1.4 | `test_slo_counts.py` |
| AC74 | T1.7 | `test_slos_live.py` |
| AC75 | T2.4 | `test_slo_suggestion.py` |
| AC76 | T4.2 | `test_alert_defaults_live.py` |
| AC77 | T3.6 | `SloScreen.test.ts` |
| AC78 | T3.3, T3.6 | `test_slos_live.py`, `SlosScreen.test.ts` |
| AC79 | T2.5 | `SloForm.test.ts` |
| AC80 | T2.2, T2.5 | `test_slos_live.py`, `SloForm.test.ts` |
| AC81, AC82, AC83 | T1.8, T2.5, T6.7 | `SlosScreen.test.ts`, `SloForm.test.ts`, design QA |
| AC84 | T4.12 | `SloScreen.test.ts` |
| AC85 | T4.11, T5.7 | `IncidentScreen.test.ts`, `FindingScreen.test.ts` |
| AC86, AC90 | T6.2 | The scale tool, in `status.md` |
| AC87 | T6.5 | `test_slo_system.py` |
| AC88 | T3.4, T3.5 | `test_slo_prune_live.py`, the personal-data tests |
| AC89 | T1.1, T4.9 | `test_slo_model.py`, `test_alert_messages.py` |
| AC91 | T4.8, T6.4 | `test_slo_blind_live.py`, the drill |
| AC92, AC100, AC103 | T4.3 | `test_slo_burn.py` |
| AC93 | T4.3, T4.5 | `test_slo_burn.py`, `test_slo_job_live.py` |
| AC94 | T4.5, T5.6 | `test_slo_job_live.py`, `test_slo_one_message_live.py` |
| AC95 | T1.5, T5.8 | `test_detection_coordinator.py`, `test_slo_regression_live.py` |
| AC96 | T1.10, T4.13 | `test_detection_has_no_ai.py`, `test_slo_ai_paths.py` |
| AC97, AC105, AC116 | T1.7 | `test_slos_live.py` |
| AC98 | T1.1, T1.8 | `test_slo_model.py`, `SlosScreen.test.ts` |
| AC99 | T5.3 | `test_slo_one_message_live.py` |
| AC101 | T2.4, T2.5 | `test_slo_suggestion.py`, `SloForm.test.ts` |
| AC102 | T4.7 | `test_slos_live.py` |
| AC104 | T4.5, T4.11 | `test_slo_job_live.py`, `IncidentScreen.test.ts` |
| AC106, AC114 | T2.5 | `SloForm.test.ts` |
| AC107 | T1.8 | `SlosScreen.test.ts` |
| AC108 | T1.8, T4.11 | `SloScreen.test.ts`, `IncidentScreen.test.ts` |
| AC109 | T4.12 | `test_slo_readings_live.py` for equality; `SloScreen.test.ts` for display |
| AC110, AC111 | T1.1 | `test_slo_model.py` |
| AC117 | T3.6 | `SloScreen.test.ts` |

## New risks

Only what this analysis found.

| Risk | Why | What limits it |
|---|---|---|
| **The withholding rule hides a message people needed** | It decides at claim on FR21's pairs | Severity may always rise. Rule gated on a burn. A 60-minute limit, a 15-minute limit on a burn, and a release. Every withheld row is logged. AC24 to AC31, AC52 to AC59 |
| **Withholding changes F6's tested path** | `claim` is the middle of delivery | T5.8: existing tests pass unedited, and again beside a burn |
| **The release writes a message that should not go** | A new writer in delivery | One statement, one marker, and AC52, AC57, AC59 |
| **The endpoint read costs more than arithmetic says** | Granules of 8,192, and most targets may leave out health checks | T6.2, and the design's fallback order |
| **`sumForEachMergeIf` might be refused** | Combinator order is untested | T1.3 proves it first, with a fallback |
| **`scheduler-light`'s 768M ceiling is a guess with SLO work added** | Nothing measured | AC50, on the deployed server; raise the ceiling and `sizing.md` together if it OOM-kills |
| **SLO judging runs late when detection, evidence and releases are busy** | Ten capped seats asked for, four given (D66) | Longest-waiting first, **Not up to date** on screen, AC65; probes and alert sweeps are not affected |
| **Suggestions look silly on real traffic** | Never tried | T2.7's judgment, and it is easy to change |
| **Coverage reads low for an unaffected tenant** | A hold is install-wide | Wording says "may have lost". Counts stand |
| **Later features move what this cites** | F28 to F5 come first | Read again at start (P1) |
| **Nobody uses it** | No evidence SLOs win a purchase | The demand gate, before the riskiest work |
| **AC49 cannot run before deployment** | No rented server (foundation item 9) | It reads "not run" until the Oracle run, and F19 is not called proved on faults |
| **No outside tester for AC38** | Nobody in the round agrees to a later screen test (spec D64) | "Not proved", and the Goal's wording stays out of any claim |

## Questions

Checked again 2026-11-02. What is left is the founder's, and none blocks writing or building.

| # | Question | Blocking | Where the answer lives |
|---|---|---|---|
| Q1 | How long monthly figures stay on hosted Free | No | **Approved 2026-11-02:** 13 months on every plan (foundation item 20) |
| Q2 | Self-hosted retention overall | No | **Settled:** foundation item 6, approved 2026-11-02. F19 does not depend on it |
| Q3 | Who owns P2, and when | **Yes, for Step 4 and release** | **When is settled:** foundation item 7, before Stage 1 reaches a customer. **Who:** F28 (foundation item 22, approved 2026-11-02). Its own record under `docs/changes/` |
| Q4 | Can a legitimate browser span be `SERVER`? | No | **Decided:** no, so P2 filters by door. `spec.md` D63 |
| Q5 | Who a figure may be quoted to | No | **Approved 2026-11-02:** internal and board only; customer quoting goes to the lawyer (foundation item 16) |
| Q6 | The demand gate's bar | No longer | **Decided:** `spec.md` D62. What to do if it fails stays the founder's at the time |

## Gates

- [x] Specification approval: 2026-11-02
- [x] Design approval: 2026-11-02
- [x] **Plan approval:** 2026-11-02
- [ ] Implementation
- [ ] Demand gate, before Step 4
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design, for customer-facing work
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

**Once, at the end of each step:** `just` targets for lint, types and offline tests across what changed (`ruff`, `mypy`, the web's typecheck, Biome and bundle sizes), and the named live tests against the dev stores. Read output and counts; an exit code is not evidence. Results go into `status.md`.

**Once at the end:** all of the above across the repository; the migrations probe; `deploy/monitoring/tests`; the fault bed; a walkthrough of every state at each of the app's widths.

**Where the long runs happen.** No test server exists, and none is rented: the founder decided on 2026-11-02 that long runs happen on Oracle Cloud after Phase 2 is deployed (foundation item 9). AC50's scale run (T6.2) can still run on the Mac's Docker, as F6's AC29 did, labelled with the machine; its figures are not published as server figures before Oracle. AC49 needs about 3 weeks of warm-up and 7 days of reference, on a machine that stays on, and the bed has not been run end to end. If F6's AC36 run has not started when Step 4 is done, F19's target is created first and that run is reused. **Without it, AC49 reads "not run", and F19 cannot be called proved on faults.**

**Not proved by any of this:** real traffic. The demo's load is steady and artificial, and a pass says little about a customer's real burn alerts. Results must say so. The spec's *Cannot be proved before a customer* is the register.

## Rollout and rollback

Pre-deployment. **The real risk is not rollout, but building the alert work for a demand nobody confirmed,** so the gate comes before it, not after Step 1 alone.

A later project-level on or off is not needed: a project with no target gets no job, and the default rule is silent. To back out of Step 5 alone, remove the `_WITHHOLD_SECOND`, `_RELEASE` and `_DROP_SNOOZED` calls and `_PIN_KEY`'s branch, and customers get both messages, which is noisy but never loses news. Snooze then does not hold burns, so that needs saying.

To stop the feature: take `slo`, `slo_counts` and `slo_history` out of `scheduler-light`'s default queues (`__main__.py`) and redeploy; queued jobs expire. Probes and alert sweeps carry on. **The migrations past Steps 1, 3 and 4 refuse to go back,** because daily counts, change history and exclusions exist nowhere else once rollups expire. Going back past them is a restore. A later migration can remove F19 forward, by a decision of its own.

## Outcome

In progress: Steps 1 to 4 on `feature/phase-2`; Step 5 built 2026-11-09 and awaiting commit (`status.md`). Filled in when the work completes.
