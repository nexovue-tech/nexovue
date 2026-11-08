# Plan: Dashboards (F20)

> **Status:** Approved by the founder, 2026-11-02, with [`spec.md`](spec.md) and [`design.md`](design.md). Rewritten that day from the
> reviewed spec and design. Nothing here has started. Ready to implement once F6 merges.
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order. Tasks under each step name the
> file that changes and the test that proves it, read from the code.

## Preconditions

- **Specification, design and this plan approved.** All three were, on 2026-11-02.
- **F6 merged** (foundation section 9). Findings, and migrations up to 0050, exist only on `feature/anomaly-detection`.
- **Step 1 needs only F6.** It ships as soon as F6 merges, ahead of Stages 2 to 5 (spec D58).
- **Step 2 onward:** ~~the one round of buyer conversations held (the foundation's founder decision 8), its notes recorded, and founder decision 1's rule applied to them, with T2.0's mapping in hand (spec D60).~~ **No longer a precondition (founder, 2026-11-08, foundation item 27):** the round is a validation milestone, and the full builder is built.
- **Step 5 onward:** **F28 merged.** T5.4 calls `markers_in_window` and `coverage_of`, and there is no fallback (D14).
- **Step 10's second task:** F19 merged.
- **Migration numbers** follow F28, F3, F22, F4, F5 and F19. Stage order applies under the approved plan.

## Current system

Read from the code on 2026-11-02, on this branch, with uncommitted work present. Line numbers will drift.

| Fact | Where |
|---|---|
| `/` serves `OverviewScreen`; `/findings` serves `FindingsScreen`; a comment says Overview holds `/` "until the insight engine exists" | `apps/web/src/app/router.tsx:236-253` |
| `NavPath` is closed, and the Home section lists `/`, `/findings`, `/alerts` | `apps/web/src/app/routes/route-manifest.ts:18-34,54-58` |
| A test holds sidebar and router together, with a list of screens reached by following something. It never renders `Sidebar`, and `app/layout/` has no Sidebar test | `apps/web/src/app/routes/route-manifest.test.ts` |
| Findings is not active on `/findings/$findingId` once it moves to `/`, since `/` is matched exactly | `apps/web/src/app/layout/Sidebar.tsx:69` |
| The 404 screen still says "Back to Overview", and the sidebar's comment is stale | `router.tsx:758-759`, `Sidebar.tsx:47-49` |
| `FindingScreen` links back to `/findings` | `apps/web/src/features/findings/FindingScreen.tsx:75` |
| The scheduler links to `/findings` in the project-level notice, and a test pins it | `apps/scheduler/src/nexovue_scheduler/alerts/messages.py:378`, `apps/scheduler/tests/test_alert_messages.py:416` |
| The scheduler's other links | `messages.py:128,130,240,325,327` |
| `SummaryState` does **not** cover no telemetry: `/services/overview` always returns totals, and `select` passes them on | `features/findings/FindingsScreen.tsx:205-233`, `features/findings/api.ts:234`, `features/findings/headline.ts:33`, `services/service.py:141-151` |
| That same answer holds `points`, which `select` discards, so the feed can draw Overview's charts with no scan | `services/schemas.py:147-155` |
| Overview's plots are inside a feature | `features/overview/overview-plots.ts`, `OverviewScreen.tsx:25` |
| Comments say a fifth read is refused. The cap is 8, and a read waits. **At two API processes the cap is 4,** so the waits stay | `overview/api.ts:143-151`, `OverviewScreen.tsx:48-51`, `FindingsScreen.tsx:48-49`, `findings/api.ts:241-244`; `ratelimit/policy.py:92`, `middleware/ratelimit.py:67`, `ratelimit/limiter.py` (`max_concurrent // instances`) |
| The limiter charges one per HTTP request, and a signed-in telemetry read waits up to 5 s **in each of two buckets** | `apps/api/src/nexovue_api/middleware/ratelimit.py:96,158-214,299-323`; `policy.py:92,99` |
| `counted`, `signed_in` and `multiplier` are local variables in the middleware | `middleware/ratelimit.py:148-156` |
| `TELEMETRY_PATHS` is matched by prefix | `ratelimit/policy.py:234-249,286` |
| The coverage test judges each `router.py` as a whole, by file | `apps/api/tests/test_ratelimit_coverage.py:38-41,48-60` |
| `POLICY_OF` lists every write route and its gate | `apps/api/tests/test_authorization_coverage.py:161` |
| A new action must change five copies | `auth/permissions.py`, `tests/test_policy.py:22-37`, `web shared/identity.ts:13-27`, `shared/identity.test-util.ts`, `features/members/MembersScreen.test.ts`, FR1 of `team-and-api-access/spec.md` |
| `project_overview` runs two statements, and tests call it directly | `services/service.py:117-120`; `tests/test_services.py:632`, `test_services_live.py:352` |
| `trend_sql` with no name returns every service, with no `LIMIT` | `services/queries.py:147,200-206` |
| `check_trends` reads every check up to 40,000 rows, from `metrics_gauge` only | `synthetics/service.py:47,67,113-132`, `synthetics/queries.py:190-215` |
| `service_findings` keeps the **oldest** first, hard-codes service scope, and its caller slices it | `detection/queries.py:105-127`, `detection/service.py:211-239` |
| Detection's status is Postgres only, and the headline is inside a feature | `detection/service.py:143`, `features/findings/headline.ts` |
| Only `detection` is held to bind the project | `apps/api/tests/test_tenant_chokepoint_structure.py:116,540-555`; `nexovue_platform/tenant_statements.py:129-131` |
| One `query` path with `READ_SETTINGS`, and a dict per row | `data/clickhouse/base.py:60-85` |
| `READ_SETTINGS` is shared with the scheduler, and its helper refuses 0 and any name that is not a bound | `tenant_statements.py:47-89`; `apps/scheduler/.../alerts/telemetry.py:120,148`, `detection/store.py:53,688`, `detection/settle.py:188,204` |
| The API and scheduler share one ClickHouse account, with no per-user limit on the server | `deploy/compose/nexovue.yaml:291,360`, `deploy/clickhouse/users.d/reader.xml` |
| The API's memory limit | `deploy/compose/nexovue.yaml:329` (832M) |
| API has 8 ClickHouse connections; the pool's Postgres wait is 10 s | `settings.py:201,209`, `lifespan.py:231-238` |
| A cold ClickHouse is retried twice and raises a critical toast | `apps/web/src/app/query-client.ts` (`shouldRetry`, `handleApiFailure`) |
| `lifespan.py` imports `mcp` to type one argument; the shared auth path reaches it | `lifespan.py:21,137`; `auth/dependencies.py:51`, `deps.py:45`; `apps/scheduler/tests/test_detection_has_no_ai.py:37-40` |
| `setAbsoluteWindow` calls `remember()` | `shared/selection.ts:150-155` |
| The range picker and the paused chip use the browser's clock, and `fixed` must be tested ahead of the stream | `app/layout/WindowControl.tsx:81-82,100-103`, `app/layout/RefreshControl.tsx:45,47,70` |
| `TIME_RANGES` stops at 7 days, and the API has no list of range ids | `shared/selection.ts:21-27` |
| uPlot's legend labels are click handlers | `apps/web/node_modules/uplot/dist/uPlot.esm.js:3259-3279` |
| `TrendChart`: fixed height, y from 0, one colour, no readout, options not exported. It has uncommitted F6 changes | `shared/components/TrendChart.tsx:21,188,345,372,377,385,395,426-432` |
| A screen can declare what the refresh control means | `shared/refresh.ts:138-185`, `app/layout/RefreshControl.tsx:31-46` |
| The expiry sweep's list | `apps/scheduler/src/nexovue_scheduler/jobs/expiry.py:32-42` |
| Detection's normal is kept one hour | `detection/baselines.py:117,142-146,1101` |
| An alert already pages on the settled minute's age | `deploy/monitoring/rules/nexovue.yml:102` |
| No dashboard table, route or screen; last migration here is 0050 | `apps/api/migrations/versions/` |
| Dashboards must join a list, or the deletion test fails | `organizations/tables.py:32`, `tests/test_deletion_live.py` |
| No drag or grid library | `apps/web/package.json:18-32` |
| Browser tests are outside `just check` and CI | `justfile:279-281`, `.github/workflows/ci.yml:77-78`, `docs/engineering/testing.md:8` |

**Corrections to what the documents said.**

| # | The document said | The code or source says | Effect |
|---|---|---|---|
| C1 | Earlier plan: `deploy_events_between`, no wait for F28 | F28's `design.md`, *Specification impact*, and D30 require `markers_in_window`, and `coverage_of` needs no ClickHouse | T5.4; F28 is a precondition |
| C2 | Earlier design: the middleware waits at most 5 s | 5 s **per bucket**, two buckets, plus an unbudgeted 10 s Postgres wait | T2.3; D25 |
| C3 | Research: batch reads "live on their own router" | The test forces a separate **file**, itself named `router.py`. The colon route made order load-bearing, and it is gone (D41) | T2.4, T2.13 |
| C4 | Research: "`HISTOGRAM_SQL`… may carry this problem" | `HEATMAP_SQL` has the same shape (`metrics/queries.py:357-360`). Still only read, never run | T8.1 |
| C5 | Research R14: tokens may read definitions | A project-scoped token would learn other projects' names (`auth/dependencies.py:174`) | No token routes |
| C6 | F6 plan C11: no shared confirm component | `ConfirmPanel` exists now (`shared/components/ConfirmPanel.tsx`) | T3.6 uses it |
| C7 | Earlier design: `service_stats_1m`'s 35 days limits compare | Only detection reads that table (`000035_service_stats_1m.up.sql:31-32`). Panels read `endpoint_stats_1m`, kept by the plan | D44; design's *A compared number* |
| C8 | Earlier design: only endpoints are approximate | Every rollup panel merges one quantile state (`services/queries.py:47`), and ClickHouse says non-deterministic | D44; T2.8 |
| C9 | A reviewer: read a normal band from `detection_baselines` | That table keeps one hour (`detection/baselines.py:117`) | D54 |
| C10 | A reviewer: add an alert on the settled minute | It exists (`nexovue.yml:102`) | Reused |

## Change required

[`spec.md`](spec.md) and [`design.md`](design.md). If this plan disagrees with them, that is a finding. *Traceability*, below, maps every requirement to its task, and T11.7 checks the map by script.

## Impact

| Area | What changes |
|---|---|
| Files | About 45 new, about 40 changed. Two new API packages, one new web feature |
| Services | API and console. **Scheduler: one link (`alerts/messages.py:378`), one sweep line (`jobs/expiry.py`), and the no-AI walker's new import.** Workers and receiver unchanged. No new role or image |
| Deployment | **One new ClickHouse account,** `nexovue_api_reader`, with its secret: `deploy/clickhouse/users.d/`, `deploy/compose/*`, `deploy/nexovue`, `deploy/release/kit.sh`, `.env.example` |
| Schema / migrations | Two Postgres tables (`dashboards`, `dashboard_versions`). No ClickHouse schema change |
| Shared code | `READ_SETTINGS` does **not** change. `ratelimit/charge.py` is new, and the middleware stores two things in `scope["state"]`. `lifespan.py` drops an import. `nexovue_platform/import_walk.py` is new. `query-client.ts` honours a query's `meta` |
| UI | `/` becomes the feed, with charts. New `/overview`, `/dashboards`, `/dashboards/new`, `/dashboards/$dashboardId`, `/dashboards/$dashboardId/edit`. Sidebar. `TrendChart` gains optional props. Buttons on the service, finding and incident screens. `selection.ts` gains a screen override, and the top bar honours its zone |
| Tests | Structural (rate limit, authorization, read surface, tenant and project, no AI, no outbound), unit (validator, compile, charge, shared reads), live (two-tenant, equal numbers, slow statements), browser (states, keyboard, escapes, 375 px), one gated measurement, run twice |

## Reuse

- **Numbers:** `services/service.py`, `servicemap/service.py`, `rum/service.py`, `infrastructure/service.py`, `synthetics/service.py`, `errorgroups/service.py`, `dbstats/service.py`, `detection/service.py`. Nothing is computed twice.
- **Limits:** `RateLimiter.consume`, `TELEMETRY_TIER`, the middleware's own slot.
- **Tenancy:** `TenantRepository`, `require_project`.
- **Markers:** F28's `markers_in_window`, `coverage_of`.
- **Components:** `TrendChart`, `DataTable`, `Delta`, `EmptyState`, `InlineFailure`, `SkeletonRows`, `Permitted`, `Refused`, `ConfirmPanel`, `SelectField`, `TextField`, `useUnsavedChanges`.
- **State:** `useSelection`, `setSelectedProjectId`, `setSelectedRangeId`, `windowCacheKey`, `declareStream` pattern, `useCan`.
- **Formatters:** `shared/format/*`, including `UNKNOWN`.
- **Patterns:** `<entity>.<past tense>` audit names (`audit/service.py`), audit rewriting (`members/service.py:225-232`), migration doc-comment shape (`0050`), `test_findings_api_live.py` for two-tenant live tests, `tools/otel-demo` for the demo.

## Gaps

Genuinely missing: stored dashboards and versions; definition validator; scan compiler and charge; shared reads; process caps; a bounded repository; a second ClickHouse account; `TOP_SERVICES_TREND_SQL` and five more statements; a general metric read and its series list; project-wide findings in a window; the screens; chart props; a screen override; templates; a repeatable demo with real findings.

**Already exists, so not built:** `GET /metrics?shape=` (`metrics/router.py:46,55`); the settled-minute alert; `service_last_seen` (`usage/postgres.go:166-175`).

## Steps and tasks

Each step is built whole, then its checks run once. Tests named `test_dashboard*` are new. Founder decision 1's fallback builds Steps 1, 2, 4 to 7 and 9, by name: **templates and the picker, with no builder, no metric series and no drag.** That path needs T2.18's small title-and-save dialog and the tasks in *The fallback's own tasks*, below, and otherwise nothing from Step 3, Step 8 or Step 10: Step 7's restore and Step 9's import then save directly, with no draft screen. `T3.1`'s preview and the side sheet would not exist, and T2.18 offers only the templates.

### The fallback's own tasks

Built only if founder decision 1 chooses templates only. The full plan skips these.

- [ ] **TF.1** — with Step 2, `features/dashboards/DashboardScreen.tsx` · mount `DashboardActions.tsx` in the read view's header menu, inside `Permitted`. On this path, T3.6's delete is built here, and T7.4, T7.7 and T7.9 reach the view through it. A 409 on a direct restore says who saved, and reloads · proved by `DashboardActions.test.ts` and a new case in `DashboardView.test.ts`: the actions show for an editor, and not for a viewer (AC78, AC105, AC136)
- [ ] **TF.2** — with Step 4, new `tools/dashboard-seed/seed.py`, `tools/dashboard-seed/definitions/`, `tools/dashboard-seed/test_seed.py` · judging data, not a product feature, and it refuses any host but localhost. It signs in as the dev stack's own account (the path is read from `tools/e2e/tests/support` when built) and saves through `POST /dashboards`, so the validator runs. One definition holds Step 4's three charts. One holds every kind on this path, including a note and the picker. It saves a second version of each · proved by `test_seed.py`, every definition passing the validator

**Judging on this path.** Steps 1, 2, 6 and 9 are judged as written. The templates cannot come first, because T9.1's templates need kinds that arrive in Step 5, and they leave some kinds out.

- *Step 4:* the founder opens TF.2's first definition in place of building it, then judges as written.
- *Step 5:* the founder opens TF.2's second definition in place of building it, then judges as written.
- *Step 7:* the bad save to restore and the note to purge are TF.2's second version. The picker is judged on TF.2's second definition. Delete, duplicate, versions and purge are reached from the read view.

**Lost on this path:** T3.5's links editor and any way to rename or edit a panel. Criteria resting only on Steps 3, 8 and 10 do not apply, and T11.7's record must list them.

### Step 1 — The front door moves to the Findings feed

*Ships alone, with no dashboard code.*
*Judgeable:* the founder opens the console, lands on the feed, sees three charts under the summary on a project with telemetry and no findings, reaches Overview in one click, and tries a project with no telemetry. An alert's detection link opens the feed.

- [x] **T1.1** — `apps/web/src/app/router.tsx` · `/` serves `FindingsScreen`; add `overviewRoute` at `/overview`; delete the `/findings` list route; rewrite the comment · proved by `route-manifest.test.ts` (AC1)
- [x] **T1.2** — `apps/web/src/app/routes/route-manifest.ts` · `NavPath` and Home: Findings at `/`, Overview at `/overview`, Alerts · proved by `route-manifest.test.ts` (AC2)
- [x] **T1.3** — `apps/web/src/features/findings/FindingScreen.tsx:75` · back link to `/` · proved by `FindingScreen.test.ts`
- [x] **T1.4** — `apps/web/src/app/layout/Sidebar.tsx:47-49,69`, `router.tsx:758` · Findings active on `/` and `/findings/$findingId`; comment and "Back to Findings" · proved by new `app/layout/Sidebar.test.ts`, a real render (AC55)
- [x] **T1.5** — `apps/scheduler/src/nexovue_scheduler/alerts/messages.py:378`, `apps/scheduler/tests/test_alert_messages.py:416` · project notice links to `{base}/` · proved by that test (AC2)
- [x] **T1.6** — new checked-in `apps/web/src/app/routes/console-routes.json`, `route-manifest.test.ts`, new `apps/scheduler/tests/test_alert_links.py` · the route list; the web test holds it to the router; the scheduler's test composes each message and matches links at `messages.py:128,130,240,325,327,378` · proved by both (FR43, AC54)
- [x] **T1.7** — `features/findings/FindingsScreen.tsx`, `features/findings/headline.ts`, `features/findings/api.ts`, `features/overview/overview-plots.ts` moved to `shared/overview-plots.ts`, `features/overview/api.ts` · `OverviewPoint` moves with it, into `shared/overview-plots.ts`, and both features' `api.ts` import it from there, because `shared/` may not import a feature; `service_count === 0` is nothing reporting; with telemetry and no findings, draw the three charts from the same answer; link to Overview; learning says when · proved by `FindingsView.test.ts`, `headline.test.ts` with the API's real shape (AC3), `OverviewView.test.ts`, which changes only its import line, with the same assertions, and a search that `apps/web/src/shared` still imports nothing from `@/features`
- [x] **T1.8** — `overview/api.ts:143-151`, `OverviewScreen.tsx:48-51`, `FindingsScreen.tsx:48-49`, `findings/api.ts:241-244` · **correct the comments only; the waits stay,** because at two API processes the cap is 4. Removing them is a separate change record · proved by review, since no behaviour changes
- [x] **T1.9** — `docs/product/feature-catalog.md`, `docs/features/console-redesign/wireframes-dashboard.md` · the front door is now as decided · proved by review

### Step 2 — One saved dashboard, read through a charged batch

*The thin path, end to end: table, permission, validator, charge, read, context, screen. Only `project_trend`, `service_trend` and `note`, and one way in: "Start from Overview", which is **a partial Overview until Step 5** (three charts, no totals or table).*
*Judgeable:* the founder clicks *Start from Overview*, saves it, sees three charts matching Overview's, reads the batch's log line (three panels, one scan), stops ClickHouse and sees the charts say they could not be read and one line saying telemetry could not be read. Notes and findings are not judged here: this dashboard has no note, nothing can add one yet, and findings have no display until T5.5 and T5.6a. Step 5 judges them. Viewer judging moves to Step 3, when there is an edit to withhold.

- [x] **T2.0** — new `docs/research/f20-board-mapping-2026-11.md` · before Step 2: take the ten boards in `tools/otel-demo/upstream/src/grafana/provisioning/dashboards/demo/` (143 panels, counted by script on 2026-11-02) and state the share mapping to a panel kind, and what is lost · proved by the report. Goes into the buyer conversations **Done 2026-11-08:** 143 panels (matches the 143); 7 map (4.9%), 68 partly (47.6%), 68 not offered (47.6%); 40 of the 75 that map or partly map need Step 8's `metric_series`
- [x] **T2.1** — `auth/permissions.py`, `auth/policy.py`, `tests/test_policy.py`, `web shared/identity.ts`, `shared/identity.test-util.ts`, `features/members/MembersScreen.test.ts`, `docs/features/team-and-api-access/spec.md` · `EDIT_DASHBOARDS`, `MayEditDashboards`, `PURGE_DASHBOARDS`, `MayPurgeDashboards` · proved by `test_policy.py`
- [x] **T2.2** — `apps/api/src/nexovue_api/data/postgres/models.py`, new migration, `organizations/tables.py` under `DELETED_DIRECTLY` · `dashboards` with `deleted_at`, `panel_count`, `service_names`, `uses_picker`, `panels_not_reading`; `dashboard_versions`; indexes · proved by `test_deletion_live.py`, two tenants (AC12)
- [x] **T2.3** — new `ratelimit/charge.py`, `middleware/ratelimit.py` · `Counted` moves; the middleware records `counted` and arrival in `scope["state"]`; `ScanCharge`, quota only · proved by new `tests/test_scan_charge.py` with the real limiter: N scans spend N in both buckets, same keys as the middleware (AC16), refusal midway (AC17), absent means off (AC60); and `test_ratelimit_callers.py` unchanged
- [x] **T2.4** — `ratelimit/policy.py` · add `/dashboard-panels` to `TELEMETRY_PATHS`; `MAX_SCANS_PER_READ`, `REFRESH_FLOOR_SECONDS`, `PANEL_READ_DEADLINE_SECONDS`, `PANEL_READ_CLIENT_TIMEOUT_SECONDS` · proved by `test_ratelimit_coverage.py` (AC25) and a new case asserting a batch at the floor fits the quota (NFR3)
- [x] **T2.5** — new `dashboards/definition.py`, `dashboards/compile.py`, `dashboards/ranges.py` · union on `kind`, `extra="forbid"`, caps, `small` refused on time panels, free-text rules, link rules, worst-case count · proved by new `tests/test_dashboard_definition.py`, parametrized from the spec's table (AC4, AC5, AC42, AC77, AC123, AC124, AC141)
- [x] **T2.6** — new `dashboards/{router,service,queries,schemas,templates}.py` · list from columns, create, read, replace with lock and no no-op version, soft delete, unique title, caps, version keeping and trimming, `GET /dashboards/templates` holding *Start from Overview* · proved by new `tests/test_dashboards_live.py` (AC6, AC7, AC10, AC11, AC96, AC125), `test_authorization_coverage.py` (`POLICY_OF`)
- [x] **T2.7** — `audit/service.py` · `DASHBOARD_CREATED`, `_UPDATED`, `_DELETED`, `_RESTORED`, `_PURGED`, with title, panel count, version, hash, link hosts · proved by `test_dashboards_live.py` (AC13)
- [x] **T2.8** — `services/service.py` · split `project_overview` into `project_totals` and `project_trend`, keeping it as a thin function composing them; **first run the live check** reading a window with over 8,192 samples twice through one route, and record the latency tolerance AC14 uses · proved by `test_services.py:632`, `test_services_live.py:352` unchanged, and the check's result in `status.md`
- [x] **T2.9** — `data/clickhouse/base.py` · `TenantScopedRepository.bounded_by(seconds)`, sending `lowered_read_settings` and the cancel setting, leaving `READ_SETTINGS` alone · proved by new `tests/test_bounded_repository.py` and a test holding `READ_SETTINGS` and `DETECTION_READ_SETTINGS` unchanged (AC93)
- [x] **T2.10** — new `dashboardpanels/caps.py` · process semaphores, 5 and 2, with an organization's share where more than one can exist · proved by new `tests/test_dashboard_caps.py`, whichever requests they belong to, and a task with no requester (AC59, AC94)
- [x] **T2.11** — new `dashboardpanels/shared_reads.py` · kept answers, in-flight joins, the rules in the design's table, row bounds and expiry, fixed statement limit · proved by new `tests/test_shared_reads.py` (AC62, AC86, AC87, AC88, AC89, AC90)
- [x] **T2.12** — new `dashboardpanels/{router,service,scans,schemas}.py` · `GET /read`, the design's sequence with the three kinds, `service_last_seen` pre-read and `not_in_project`/`empty`, stamp and commit before scans, deadline from arrival, under-1-s rule, error mapping, panel-boundary catch, `over_limit` past the cap, the batch's log line with a test on its fields · proved by new `tests/test_dashboard_panels.py` (AC15, AC19, AC20, AC79, AC92, AC130), new `tests/test_dashboard_panels_live.py` (AC8, AC14 for these kinds, AC21, both of AC84's cases, holding real slots in the limiter), new `tests/test_dashboard_panels_seams.py` using the test seam below (AC18, AC63)
- [x] **T2.13** — `apps/api/src/nexovue_api/main.py` · include both routers · proved by new `tests/test_dashboards_routing.py`, through `create_app()` (AC24)
- [x] **T2.14** — `dashboards/router.py`, `dashboards/service.py`, `detection/queries.py` · `GET /dashboards/{id}/context`: project, clock, retention, detection's state, `ever_reported`, `last_seen_at`, new `project_findings_in_window`, open counts; **`service_findings` unchanged** · proved by `test_dashboards_live.py`, ClickHouse pointed at an unreachable address (AC61), `test_detection_status_api_live.py` unchanged, and a new live case at over 200 findings on both reads
- [x] **T2.15** — `dashboards/service.py`, `errors/problems.py` · `dashboard-not-found`, `project-not-found` problem types · proved by `test_dashboards_live.py` (AC75)
- [x] **T2.16** — new structural rule in `tests/test_read_surface_structure.py`, new `tests/test_dashboards_real_tokens_live.py` · every F20 handler annotates its gate; each kind of real token refused on each route, spending no scan · proved by themselves (AC9)
- [x] **T2.17** — unless F5 or F19 landed first (F19 D55): `lifespan.py` (Protocol, drop the import), new `packages/py-platform/src/nexovue_platform/import_walk.py`. Then: `apps/scheduler/tests/test_detection_has_no_ai.py` importing it, new `apps/api/tests/test_dashboards_have_no_ai.py` · the walk, and a control finding AI from `nexovue_api.assistant.server`, with detection's own test and control still passing; once F4 has merged, the same test checks `TICKET_HANDLERS` · proved by both (AC53). **Done 2026-11-08:** F19 had already landed the Protocol and `import_walk.py` (its D55), so this step added only `test_dashboards_have_no_ai.py` with its control; `TICKET_HANDLERS` does not exist yet
- [x] **T2.18** — `apps/web/src/app/router.tsx`, `route-manifest.ts`, `app/query-client.ts`, new `features/dashboards/{DashboardScreen.tsx,DashboardListScreen.tsx,TitleDialog.tsx,api.ts,layout-rows.ts,panels/}`, new `shared/format/visible-escapes.ts`, new `shared/detection/headline.ts` moved from `features/findings/headline.ts`, `features/findings/api.ts` · `ProjectTotals`, `CheckCounts`, `DetectionStatus` and `DetectionState` move into the headline module, and `findings/api.ts` imports them, because `shared/` may not import a feature. Read view with `<section>` and `<h2>`, list with one action, `Panel` states, `timeoutMs`, `meta` giving no retry and no toast on 503 and no toast on 429 with FR45's sentence (AC145), one line saying telemetry could not be read (AC61's, plain until T6.6 orders it), title-and-save dialog, escapes, detection line (AC97), "could not be read" line (AC100) · proved by new `features/dashboards/DashboardView.test.ts`, through the real `createQueryClient` (each state, BR5, AC20, AC61's no retry and no toast, AC111, AC145's sentence and no toast), `layout-rows.test.ts` (AC67), `visible-escapes.test.ts`, `route-manifest.test.ts`, `features/findings/headline.test.ts` moved, changing only its import line, and the same search as T1.7. **Done 2026-11-08, two layout corrections:** the panel components sit in the feature folder itself (`TimePanel.tsx`, `NotePanel.tsx`, `PanelFrame.tsx`), because `biome.json` holds feature folders flat (no `../` import); and the view test is `apps/web/src/app/DashboardView.test.ts`, because it uses the real `createQueryClient` and a feature may not import `app/`. Later steps naming `panels/` take the flat path
- [x] **T2.19** — new `tests/test_dashboard_outbound_imports.py` · no outbound client in either package · proved by itself (AC142, its structural half)

**The test seam (AC18, AC19, AC63, AC72, AC84, AC89, AC92).** Dev data is small and statements are fixed. So: a test-only tenant-bound slow statement, added to the readers' table by a test fixture and never by the app; a real uvicorn server with a client that really drops; an API instance pointed at an unreachable ClickHouse address for AC61; and a project with old data for AC70, instead of stopping shared ingest, since live suites share one stack across six workers (`justfile`, `pytest -n 6`).

### Step 3 — The builder

*Judgeable:* the founder creates a dashboard using only the keyboard: adds a panel with a real service, moves it, widens it, previews, saves, reloads. A viewer sees no edit, and their `/edit` link is refused.

- [ ] **T3.1** — `dashboardpanels/router.py` · `POST /preview`, gated, sharing the service · proved by `test_authorization_coverage.py`, `test_dashboard_panels.py` (a draft charges as a read; AC8, AC135)
- [ ] **T3.2** — new `features/dashboards/DashboardEditScreen.tsx`, `features/dashboards/draft.ts` · pure reducer: add, move, resize, remove, duplicate · proved by new `draft.test.ts`, including "dirty means differs from stored" (AC133)
- [ ] **T3.3** — new `features/dashboards/PanelPicker.tsx`, `SideSheet.tsx` · `clamp` width, real values through a native select with escaped labels, separate typed field, its six states; **try the control on phone Safari and Chrome before relying on it** · proved by new `PanelPicker.test.ts` (AC112, AC134), `tools/e2e/tests/dashboards-picker.spec.ts` on Chromium (AC74), and a hand check on phone Safari recorded in `status.md` with device and date (AC74)
- [ ] **T3.4** — `features/dashboards/DashboardEditScreen.tsx` · toolbar, one tab stop, names, focus kept, live region · proved by new `DashboardEditScreen.test.ts` using the render harness (AC37, AC143) and `tools/e2e/tests/dashboards-keyboard.spec.ts`
- [ ] **T3.5** — same · `useUnsavedChanges`, `Permitted`, conflict prompt, validation summary, links editor · proved by `DashboardEditScreen.test.ts` (AC7, AC10, AC105)
- [ ] **T3.6** — new `features/dashboards/DashboardActions.tsx`, mounted by `DashboardEditScreen.tsx` · delete through `ConfirmPanel`, with real numbers, export offered. A component of its own, so TF.1 can mount it on the read view · proved by new `DashboardActions.test.ts` (AC136)
- [ ] **T3.7** — **A one-day trial, recorded in `status.md`:** reorder panels of mixed widths with `@dnd-kit/core` 6.3.1 and `@dnd-kit/sortable` 10.0.0 (MIT, peers `react >=16.8.0`, read from npm on 2026-11-02, not tried with React 19), on a throwaway branch · proved by the record, naming pass or fail. **Stop rule:** if it cannot reorder mixed widths reliably, or takes over a day, Step 10's first task is dropped, the buttons stay, and the founder is told (AC144)
- [ ] **T3.8** — `features/dashboards/DashboardEditScreen.tsx` · below `lg` moves read Up and Down, width hidden; one menu per panel; full-screen sheet · proved by `DashboardEditScreen.test.ts` and T6.8's 375 px run (AC108)

### Step 4 — The chart does a dashboard's job

*The two kinds it needs to be judged are built first.*
*Judgeable:* the founder opens a dashboard with a tall chart, a top-5 chart in five colours and a gauge off zero; moves the cursor over one and reads the same moment on all; drags, presses Back and returns; every other screen looks the same as before.

- [ ] **T4.1** — `services/queries.py`, `services/service.py`, `dashboardpanels/scans.py`, `dashboards/definition.py`, `tests/test_tenant_chokepoint_structure.py` · `TOP_SERVICES_TREND_SQL`, with rank and project in the store; F20's named statement list and its project check · proved by the structure test (AC85, NFR2), a two-project live case (AC26)
- [ ] **T4.2** — `infrastructure/queries.py`, `infrastructure/service.py`, `dashboardpanels/scans.py` · `NODE_TREND_SQL`, one node and metric · proved by the structure test, a live case with a host late in the alphabet (AC58)
- [ ] **T4.3** — new `shared/components/trend-chart-options.ts`, `TrendChart.tsx` · **after F6 merges,** export `chartOptions` unchanged and commit a snapshot of its output · proved by new `trend-chart-options.test.ts` against that snapshot
- [ ] **T4.4** — same · add `height`, `yRange`, `distinctColours`, `readout`, `syncKey`, optional span and marker names, dotted seen rules · proved by the same test, with no new prop equal to the snapshot (AC43, its data half). The screens: `grep -rl TrendChart apps/web/src` gives the list for Design QA's screenshots
- [ ] **T4.5** — `shared/selection.ts` · the screen override: unremembered project and window, and a declared zone · proved by a new case in `selection.test.ts`, `remember()` never seeing it (AC69, its data half)
- [ ] **T4.6** — `features/dashboards/panels/TimePanel.tsx`, `shared/drag-window.ts` · shared `syncKey`; drag through the override, rounded out, reading only visible panels, not `setAbsoluteWindow` · proved by new `TimePanel.test.ts` for options and rounding, the **data half only** of AC31, since jsdom has no canvas
- [ ] **T4.7** — new `features/dashboards/panels/ChartLegend.tsx` · `legend.show: false`, `setCursor` hook, one row per line, real links, names at the cursor · proved by new `ChartLegend.test.ts` (AC106, and AC107's readout, from data, since jsdom has no canvas)
- [ ] **T4.8** — `features/dashboards/panels/ChartTable.tsx` · table view through `DataTable`, full row width, a finding and release column, latency marked as an estimate · proved by new `ChartTable.test.ts` (AC34, AC113)
- [ ] **T4.9** — `services/service.py` reader wrapping in `dashboardpanels/scans.py`, `TimePanel.tsx` · `incomplete_from`, drop that bucket, "so far" · proved by a fixture test in `test_dashboard_panels_live.py` at 1 and 24 hours (AC114). Overview's own fault: a new `docs/changes/<id>/bug.md`, not fixed here
- [ ] **T4.10** — **Browser check, recorded in `status.md`,** in `tools/e2e/tests/dashboards-chart.spec.ts` · 30 charts with a shared cursor: smooth on the founder's machine? One drag makes exactly one window change, one address change and one batch? A tap shows a value? · proved by a Chrome performance trace and the spec (AC31's drawing half), and for a tap, the real phones under *What these tests cannot prove* (AC107). The trace's reading sets the cap if it is not smooth, and a phone uses the table if touch fails

### Step 5 — Every panel kind, marks on charts, and the measurement

*Judgeable:* the founder builds a dashboard from every kind, on the demo, and compares each with its own screen; sees deploy rules, a seen version, shaded findings and an alert incident in the strip; stops ClickHouse and sees the notes and findings stay; and reads the measured numbers that replace the guesses.

- [ ] **T5.1a** — `services/queries.py`, `services/service.py` · `ENDPOINT_TREND_SQL`, bound to one service and endpoint · proved by the structure test and `test_dashboard_panels_live.py` (AC14 for this kind)
- [ ] **T5.1b** — `infrastructure/queries.py`, `infrastructure/service.py` · `TOP_NODES_TREND_SQL`, ranking in the store · proved by the structure test and a live case: each line equals that node's trend (AC14, AC26)
- [ ] **T5.1c** — `synthetics/queries.py`, `synthetics/service.py` · `CHECK_TREND_SQL`, one `check_id`, resolved in the pre-read, and two-match `ambiguous` · proved by the structure test and a live case against the existing route's values (AC14, AC22, AC95)
- [ ] **T5.1d** — `dbstats/queries.py`, `dbstats/service.py` · the statement trend, one hash and server · proved by the structure test and a live case (AC14)
- [ ] **T5.2** — `dashboardpanels/scans.py`, `dashboards/compile.py`, `dashboards/definition.py` · readers over existing functions: `project_totals`, `service_totals`, `services_ranked`, `endpoints_ranked`, `dependencies`, `web_vitals`, `error_groups`, `slow_statements`. Read each reused statement's predicates, and add them to the named list · proved by `test_dashboard_panels_live.py` (AC14 for each, AC39, AC85)
- [ ] **T5.3** — `dashboardpanels/service.py`, `scans.py` · compare with `previous_coverage`, and raw window limits as the lesser of safety and plan · proved by `test_dashboard_panels_live.py` (AC23, AC73, AC131)
- [ ] **T5.4** — `dashboards/service.py`, `dashboards/schemas.py` · `markers_in_window`, `coverage_of`, `basis`, `state`, alert incidents, service-name-free labels · proved by `test_dashboards_live.py`: context answers with ClickHouse unreachable while returning markers (AC32's data half, AC33's data half, AC61, AC117, AC122, AC132)
- [ ] **T5.5** — new `shared/finding-spans.ts`, `features/servicedetail/service-detail.ts`, new `features/dashboards/WindowStrip.tsx`, `panels/TimePanel.tsx` · move `findingSpans`, keeping ids, with its own `MS_PER_SECOND`. No type moves, since `FindingMarker` and `ChartSpan` are already in `shared/` (`service-detail.ts:9-10`); the strip, open findings first; FR70's line · proved by `service-detail.test.ts`, new `WindowStrip.test.ts` (AC65, AC66, AC113), `tools/e2e/tests/dashboards-marks.spec.ts` (AC32, AC33's drawing halves, AC61's findings half)
- [ ] **T5.6a** — `features/dashboards/panels/` · number, table and open-findings components, `Delta`, kind-by-kind words, viewer words · proved by `DashboardView.test.ts` (AC36, AC97, AC99, AC105, AC109) and `tools/e2e/tests/dashboards-hostile-names.spec.ts`, parametrized over each value, on Chromium (AC35, AC74), and a hand check on phone Safari recorded in `status.md` with device and date (AC74)
- [ ] **T5.6b** — new `deploy/clickhouse/users.d/api-reader.xml`, `deploy/compose/nexovue.yaml`, `deploy/compose/docker-compose.yaml`, `compose.infra.yaml`, `compose.infra.deploy.yaml`, `deploy/nexovue`, `deploy/release/kit.sh`, `deploy/compose/.env.example`, `apps/api/tests/environment.py`, `apps/api/tests/test_clickhouse_reader_live.py`, `dashboardpanels/service.py` · `nexovue_api_reader`, with `max_memory_usage_for_user` and `max_concurrent_queries_for_user`; read how `clickhouse_connect` surfaces the codes, and map 202, 159 and 241. **Read the settings against the pinned 26.3 image first** · proved by `test_compose_contract.py`, `test_install_script.py`, `test_cross_service_contracts.py` if it applies, and T5.7 (AC91). The live suites run on the dev stack's new account. T5.7 runs on the production replica, `just dev-app` (`nexovue.yaml`), which carries the API's memory limit
- [ ] **T5.6c** — `settings.py`, `dashboardpanels/service.py` · `dashboard_scans_enabled`, and self-hosted settings for caps and floor, refused at start if they break the quota's arithmetic · proved by `test_dashboard_panels.py` (AC116) and a settings test
- [ ] **T5.7** — new `tools/dashboard-load/`, committed under `docs/research/`. **Runs last in this step, and it is a gate.**
  1. **First, commit a pass-mark file** in `docs/research/`: numeric margins, machine, ClickHouse ceiling, data volume, and the incident script. *No test server is rented (foundation item 9, 2026-11-02), so it runs on the Mac and says so: it shares 11.7 GiB with the demo (`phase-2-plan.md`). The caps it sets are build values; it is rerun on Oracle after deployment, against the same pass marks, before any cap is published.*
  2. Run a no-load baseline, then 1, 5 and 20 worst-case dashboards, at 1 and 2 API processes. Record NFR11's named measures, ClickHouse memory, refused count, connection queueing, **API peak memory with the shared-read store full,** real version storage, and the findings read's plan.
  3. NFR12's two cases: 30 people, and 10 zooming. The incident script. Two tenants at their limits (AC94). API reads saturated, confirming inserts and the detection sweep continue (AC91).
  4. `node_trend`, `top_nodes_trend` and `check_trend` at 7, 30 and 90 days. One finding's window against its chart (AC113).
  5. Each template opened by one person, with no refused panel (AC129). Statement cancellation in `system.processes` (AC63).
  - Proved by the report, which says demo volume does not prove customer volume, and what must trigger a rerun (a change to caps, statements or sizing) (AC126, AC127, NFR4). **It sets every value marked *guess* in the design's table.** Caps are set from the report if every template shows no `refused` panel for one person and NFR12's two cases pass; only if NFR12 cannot pass at any cap does it come back to the founder (spec D59). A failed margin stops release at those caps

### Step 6 — The address, refresh and staying honest

*Judgeable:* the founder pastes a link into a second browser with another project selected, and sees the same project and moment; stops the API and watches the line and banner appear in order; pauses; opens a phone view at 375 px; and sees the top bar agree with the page throughout, on the project's clock.

- [ ] **T6.1** — `app/router.tsx` · `validateSearch` for `project`, `range`, `from`, `to`, `service`, `display` · proved by a new case in `use-dashboard-address.test.ts`
- [ ] **T6.2** — new `features/dashboards/use-dashboard-address.ts` · the address wins for this page; no read until store and address agree; drag pushes one history entry; later changes replace · proved by new `use-dashboard-address.test.ts` (AC27, AC28, AC69, AC103), `tools/e2e/tests/dashboards-link.spec.ts`
- [ ] **T6.3** — `shared/refresh.ts`, `app/layout/RefreshControl.tsx` · third declared kind, fixed first; per-tab interval; "Read now"; "Fixed time" · proved by `RefreshControl.test.ts`, `refresh.test.ts`, one test per row of the state table (AC29, AC104, AC118)
- [ ] **T6.4** — `features/dashboards/api.ts` · own interval, per-panel last-good merge, stale banner, catch-up reads, a whole-request 429 waiting for `Retry-After`, one shrink and regrow, fixed-window rule, 25 s timeout's state · proved by new `features/dashboards/api.test.ts` (AC30, AC71, AC98, AC110, AC111, AC145's wait)
- [ ] **T6.5** — `DashboardScreen.tsx` · copy link, both choices; project-404 page state; a link's unknown `service=` · proved by `DashboardView.test.ts` (AC75, AC123, AC137), `tools/e2e/tests/dashboards-link.spec.ts`
- [ ] **T6.6** — new `features/dashboards/PageLine.tsx` · priority table, one severe line, merged context, role words, never-reported states, `needs_service`, FR54's line · proved by new `PageLine.test.ts` (AC70, AC72, AC105, AC109, AC140) and T6.8's run (AC101)
- [ ] **T6.7** — `app/layout/WindowControl.tsx`, `RefreshControl.tsx`, `TopBar.tsx` · the screen's declared zone in the phrase, chip and range picker · proved by `RefreshControl.test.ts`, new `WindowControl.test.ts` (AC64)
- [ ] **T6.8** — `DashboardScreen.tsx`, `tools/e2e/tests/dashboards-phone.spec.ts` · 375 px layout, sticky line, menu, links outside it, 44 px controls, panels drawn as they scroll in · proved by a Playwright run at 375 x 667 (AC68, AC100, AC101, AC108)
- [ ] **T6.9** — `DashboardScreen.tsx`, `app/layout/AppShell.tsx` · `?display=1`, kept lines, Escape, "Leave full screen" · proved by `DashboardView.test.ts` and `tools/e2e/tests/dashboards-display.spec.ts` (AC76)
- [ ] **T6.10** — `DashboardScreen.tsx`, `dashboards/service.py` · note wording by install, browser check that requests stay on the install's own address · proved by `DashboardView.test.ts` (AC119) and `tools/e2e/tests/dashboards-display.spec.ts` (AC142, its browser half)

### Step 7 — Staying few and recoverable: list, last opened, picker, versions

*Judgeable:* the founder sorts the list by last opened, sees a stale tag and "3 panels not reading"; leaves a dashboard open and sees last opened move; switches a single dashboard between two services by the picker; restores a bad save and a deleted dashboard; opens a board from a finding and from an alert incident; and purges a note.

- [ ] **T7.1** — `dashboardpanels/service.py` · hourly stamp already built in T2.12, now `panels_not_reading` after assembling · proved by `test_dashboard_panels_live.py` (AC45, AC115)
- [ ] **T7.2** — `dashboards/queries.py`, `features/dashboards/DashboardListScreen.tsx`, `shared/components/DataTable.tsx` · columns, sort buttons with `aria-sort`, tag, former member, filter, states · proved by new `shared/components/DataTable.test.ts`, new `DashboardListScreen.test.ts` (AC45, AC46, AC47, AC81, AC96)
- [ ] **T7.3** — `dashboards/definition.py`, `dashboards/compile.py`, `DashboardScreen.tsx` · picker through a native select, debounce, `needs_service` · proved by `DashboardView.test.ts` (AC44, AC140). On the fallback, T3.3 is not built, so AC74's phone Safari check of the picker is done here
- [ ] **T7.4** — `dashboards/router.py`, `service.py`, `features/dashboards/Versions.tsx`, opened from `DashboardActions.tsx` · version routes, restore a version as a draft, or directly with no builder · proved by `test_dashboards_live.py` and new `Versions.test.ts` (AC56, AC125)
- [ ] **T7.5** — `dashboards/router.py`, `service.py`, `features/dashboards/RecentlyDeleted.tsx` · `?deleted=true`, `POST /restore`, title conflict, caps · proved by `test_dashboards_live.py`, new `RecentlyDeleted.test.ts` (AC11, AC57, AC78)
- [ ] **T7.6** — `apps/scheduler/src/nexovue_scheduler/jobs/expiry.py`, new `apps/scheduler/tests/test_dashboards_expiry_live.py` · the sweep line · proved by that test: a dashboard and its versions at 29 and 31 days (AC57)
- [ ] **T7.7** — `dashboards/router.py`, `service.py`, `features/dashboards/Versions.tsx` · `POST /purge`, one audit entry, audit rewriting, members refused · proved by `test_dashboards_live.py`, including each read route after purge (AC120)
- [ ] **T7.8** — `dashboards/router.py`, new `features/dashboards/DashboardsShowingThisService.tsx`, `features/findings/FindingScreen.tsx`, `features/servicedetail/ServiceDetailScreen.tsx`, `features/alerts/IncidentScreen.tsx` · `?service=`, naming first, "See all", window from an open or closed finding · proved by `test_dashboards_live.py`, new `DashboardsShowingThisService.test.ts`, `IncidentScreen.test.ts`, `FindingScreen.test.ts` (AC82, AC102, AC117)
- [ ] **T7.9** — `features/dashboards/DashboardActions.tsx` · duplicate, asking for a new title and showing the existing ones · proved by `DashboardActions.test.ts` (AC78)

### Step 8 — Metric series

*Judgeable:* the founder draws a counter from the demo, restarts that service, and sees no spike, against a number worked out by hand in the report. Choosing a series and adding an attribute reads as changed, not as absent.

- [ ] **T8.1** — New `tests/test_metric_series_live.py`, writing its own fixture to `metrics_sum` and `metrics_gauge` · first, records whether `HISTOGRAM_SQL` and `HEATMAP_SQL` subtract across instances · proved by its output. **If real: a separate `docs/changes/<id>/bug.md`, not fixed here.** The demo stays for the founder's judging only. It also settles what a later `histogram_quantile_trend` would need (D49)
- [ ] **T8.2** — `metrics/queries.py`, `metrics/service.py`, `dashboardpanels/scans.py` · gauge and cumulative series, one bucket read before, restart rule, unit check, combining all into one line, the cap, `too_many_series`, `series_changed`'s second read · proved by `test_tenant_chokepoint_structure.py` and `test_metric_series_live.py` (AC48, AC49, AC50, AC51, AC52, AC115)
- [ ] **T8.3** — `metrics/router.py`, `metrics/queries.py`, `metrics/service.py`, `tests/test_read_surface_structure.py` · new `GET /metrics/{metric_name}/series`, `CurrentIdentity`, one service, 7 days, 200 combinations · proved by `test_metric_series_live.py` and `test_dashboards_real_tokens_live.py` (AC9, AC83, AC121)
- [ ] **T8.4** — `features/dashboards/PanelPicker.tsx` · names through the existing `GET /metrics?shape=`, and the series list, with escapes · proved by `PanelPicker.test.ts`

### Step 9 — Starting points, export and import

*Judgeable:* the founder opens *Service health* from a service's screen; opens each template; exports a dashboard, imports it and sees it equal; tries a hostile file and sees nothing opened.

- [ ] **T9.1** — `dashboards/templates.py`, `features/dashboards/DashboardListScreen.tsx` · *Service health* and *Infrastructure*, exactly as the spec lists, and the rest of *Start from Overview*; the empty states' order · proved by a parametrized test in `test_dashboard_definition.py` (AC38, AC80) and `test_dashboard_panels_live.py` (AC39, AC129's logic), `DashboardListScreen.test.ts`
- [ ] **T9.2** — `features/servicedetail/ServiceDetailScreen.tsx` · start a dashboard, or open the one there is, hidden from a viewer (`useCan`) · proved by a new `ServiceDetailScreen.test.ts` (AC38, AC138)
- [ ] **T9.3** — `dashboards/router.py`, `dashboards/service.py`, `features/dashboards/transfer.ts`, `features/dashboards/DashboardListScreen.tsx` · `POST /dashboards/validate`, then the draft; export with its warning; no import for a viewer · proved by `test_dashboards_live.py`, `test_authorization_coverage.py` (AC128), new `transfer.test.ts` (AC40, AC41, AC119, AC139), a two-organization live case (AC40)

### Step 10 — Drag to reorder, and F19's panel

*Judgeable:* the founder drags a panel, the same order saves, and buttons still work with the mouse unplugged.

- [ ] **T10.1** — **Only if T3.7 passed.** `apps/web/package.json`, `features/dashboards/DashboardEditScreen.tsx` · reorder over the same stored order · proved by `tools/e2e/tests/dashboards-drag.spec.ts`, and AC37 unchanged (AC144). It must not grow into a grid
- [ ] **T10.2** — `dashboards/definition.py`, `dashboards/compile.py` · `slo_budget`, if F19 has merged · proved by the validator test and F19's own read

### Step 11 — Documentation, the demo gate and readiness

*Judgeable:* a person new to the product follows the docs, and the founder gives the demo from the running product.

- [ ] **T11.1** — `docs/product/setup/`, `docs/operations/monitoring.md` · the design's whole docs list: what a dashboard is, limits and why, the fixed panel list, latency as an estimate, a wall display on a viewer account, what a copied link contains, nothing leaves the install, attribute labels, purge and backups, the mapping from common panels naming no rival, and a release note stating what a buyer will ask: no query language, no public links, no dashboards as code · proved by review
- [ ] **T11.2** — `docs/product/phase-2-foundation.md`, `feature-catalog.md`, `docs/features/personal-data/spec.md` · as the design's *Specification impact* · proved by review
- [ ] **T11.3** — `docs/self-hosting/sizing.md` · the new account and the settings, and whatever T5.7 requires · proved by the report
- [ ] **T11.4** — **The first demo gate, after Steps 1 to 9.** New `tools/dashboard-demo/`, `tools/detection-seed/seed.py` · a repeatable demo project, labelled as demo data: over 3 days of history through ingest or the seed writing `service_last_seen` rows, one real finding, one signed deploy, on the *Service health* template. The buyer table in `spec.md` is answered from the running product, and a signed checklist in `status.md` must exist before anyone says "has dashboards" · proved by the checklist and `tools/detection-seed/test_seed.py`
- [ ] **T11.5** — `docs/features/dashboards/research.md`, foundation section 12 · re-read every competitor fact against the vendor's own page, dated, before any wording enters section 12 or is said in public, and only wording the founder approved (spec, founder decision 4) · proved by review
- [ ] **T11.6** — T5.7's tool · **rerun as a gate** after Steps 8 and 9, against the same pass marks, adding metric series, catch-up reads and templates · proved by a second report. A failed margin stops release
- [ ] **T11.7** — **Screen reader pass, recorded.** One named reader on the dashboard view and builder. And a script run on these documents: every FR, NFR and AC in `spec.md` appears in *Traceability* below · proved by the record and the script's output

## Traceability

Every requirement, its task and its proof. **AC** rows are the proof's own name. A task's tests are named above.

| Acceptance criteria | Task | Gate |
|---|---|---|
| AC1, AC2 | T1.1, T1.2, T1.5 | `just check` |
| AC3 | T1.7 | `just check` |
| AC54 | T1.6 | `just check` |
| AC55 | T1.4 | `just check` |
| AC4, AC5, AC42, AC77, AC123, AC124, AC141 | T2.5, T6.5 | `just check` |
| AC6, AC7, AC10, AC11, AC13, AC96 | T2.6, T2.7, T3.5, T7.2, T7.5 | Live, `just check` |
| AC8, AC79 | T2.12, T3.1 | Live, `just check` |
| AC9 | T2.16, T8.3 | Live, real tokens |
| AC12 | T2.2 | Live |
| AC14 | T2.8, T2.12, T5.1a to T5.2 | Live, with a stated latency tolerance |
| AC15, AC19, AC20, AC92, AC130 | T2.12, T2.18 | `just check`, live |
| AC16, AC17, AC60 | T2.3 | `just check` |
| AC18, AC63 | T2.12's test seam, T5.7 | Live |
| AC21 | T2.12 | Live, through ingest and the meter |
| AC22, AC95 | T5.1c | Live |
| AC23, AC73, AC131 | T5.3 | Live, `just check` |
| AC24 | T2.13 | `just check` |
| AC25 | T2.4 | `just check` |
| AC26, AC85 | T4.1, T5.1b, T5.2 | `just check`, live |
| AC58 | T4.2 | Live |
| AC59, AC94 | T2.10, T5.7 | `just check`, gate |
| AC61 | Data: T2.14, T5.4. No retry, no toast, the line: T2.18. Notes and findings drawn: T5.5, T5.6a | Live, `just check` |
| AC62, AC86, AC87, AC88, AC89, AC90 | T2.11, T5.7 | `just check`, gate |
| AC84 | T2.12, T2.18 | Live, `just check` |
| AC145 | T2.18, T6.4 | `just check` |
| AC91 | T5.6b, T5.7 | Contract test, gate |
| AC93 | T2.9 | `just check` |
| AC116 | T5.6c | `just check` |
| AC27, AC28, AC69, AC103 | T4.5, T6.2 | `just check`, browser |
| AC29, AC104, AC118 | T6.3 | `just check` |
| AC30, AC71, AC98, AC110 | T6.4 | `just check` |
| AC31 | T4.6, T4.10 | Data: `just check`. Drawing: browser |
| AC32, AC33 | T5.4, T5.5 | Data: live. Drawing: browser |
| AC34 | T4.8 | `just check` |
| AC35, AC74 | T2.18, T3.3, T5.6a | Render: `just check`. Canvas: browser, Chromium. Phone Safari: recorded hand check |
| AC36 | T5.6a | `just check` |
| AC64 | T6.7 | `just check` |
| AC65, AC66 | T5.5 | `just check` |
| AC67 | T2.18 | `just check` |
| AC68, AC100, AC101, AC108 | T2.18, T3.8, T6.8 | Browser |
| AC70, AC72, AC105, AC109, AC140 | T2.18, T3.5, T6.6, T7.3 | `just check`, live |
| AC75 | T2.15, T6.5 | Live, `just check` |
| AC76 | T6.9 | Browser |
| AC97, AC99 | T2.18, T5.6a | `just check` |
| AC102, AC82, AC117 | T5.4, T7.8 | Live, `just check` |
| AC106 | T4.7 | `just check` |
| AC107 | T4.7, T4.8, T4.10 | `just check`; a tap: recorded hand check |
| AC111 | T2.18, T6.4 | `just check` |
| AC112, AC134 | T3.3 | `just check`, browser |
| AC113 | T4.8, T5.5, T5.7 | `just check`, gate |
| AC114 | T4.9 | Live |
| AC115 | T7.1, T8.2 | Live |
| AC119 | T6.10, T9.3 | `just check` |
| AC120 | T7.7 | Live |
| AC121 | T8.3 | Live |
| AC122, AC132 | T4.4, T5.4, T5.5 | Live, `just check` |
| AC125, AC56 | T2.6, T7.4 | Live |
| AC128 | T9.3 | Live |
| AC137 | T6.5 | `just check` |
| AC142 | T2.19, T6.10 | `just check`, browser |
| AC37, AC143 | T3.4 | `just check`, browser; screen reader T11.7 |
| AC133, AC135, AC136 | T3.1, T3.2, T3.6, TF.1 | `just check` |
| AC38, AC39, AC80, AC129, AC138 | T5.2, T9.1, T9.2, T5.7 | `just check`, live, gate |
| AC40, AC41, AC139 | T9.3 | `just check`, live |
| AC43 | T4.3, T4.4 | `just check`; screens in Design QA |
| AC44 | T7.3 | `just check` |
| AC45, AC46, AC47, AC81 | T7.1, T7.2 | Live, `just check` |
| AC57, AC78 | T7.5, T7.6, T7.9, TF.1 | Live, `just check` |
| AC48, AC49, AC50, AC51, AC52, AC83 | T8.1, T8.2, T8.3 | Live |
| AC53 | T2.17 | `just check` |
| AC126, AC127 | T5.7, T11.6 | Gate, committed report |
| AC144 | T3.7, T10.1 | Recorded |

| Functional requirements | Task |
|---|---|
| FR1, FR2, FR3, FR4, FR43, FR44 | T1.1 to T1.7 |
| FR5, FR6, FR7, FR8, FR9 | T2.5, T2.6, T4.1, T4.2, T5.1a to T5.2, T7.5, T8.2 |
| FR10, FR11, FR12, FR13, FR14, FR15, FR16, FR17, FR18 | T2.3, T2.10, T2.11, T2.12, T5.3, T5.4 |
| FR19, FR20, FR21, FR23, FR24, FR25, FR26, FR27 | T2.6, T2.7, T3.1 to T3.6, T7.9 |
| FR22 | T3.7, T10.1 |
| FR28, FR29, FR30, FR31, FR45, FR46, FR48, FR52, FR53, FR54, FR59, FR60, FR65, FR71 | T4.5, T6.1 to T6.7 |
| FR32, FR34, FR35, FR47, FR66, FR70 | T4.4 to T4.9, T5.5 |
| FR33 | T5.7, T11.6 |
| FR36, FR37, FR38, FR51, FR57, FR58, FR67, FR68 | T5.4, T7.1 to T7.8 |
| FR39, FR40, FR41, FR42 | T5.2, T9.1, T9.2, T9.3 |
| FR49, FR50, FR55, FR56, FR61, FR62, FR63, FR64 | T2.5, T2.14, T2.18, T6.8, T6.9, T6.10 |
| FR69 | T5.6c |

| Non-functional requirements | Task |
|---|---|
| NFR1, NFR9 | T2.2, T2.6 |
| NFR2 | T4.1, T5.1a to T5.2 |
| NFR3 | T2.4 |
| NFR4, NFR11, NFR12 | T5.7, T11.6 |
| NFR5 | T2.18, T3.4, T11.7 |
| NFR6 | T2.18 |
| NFR7 | T4.3, T4.4 |
| NFR8 | T2.17 |
| NFR10 | T2.12, T3.1 |
| NFR13 | T2.19, T6.10, T11.1 |
| NFR14 | T5.6b, T5.7 |

## New risks

- **The numbers may not hold.** Every cap is a guess until T5.7. A cap too low for a useful dashboard forces a choice between load and usefulness.
- **30 uPlot charts with a shared cursor may be slow** (T4.10). Unknown. A fall-back is a lower panel cap.
- **The new read account covers all API screens.** A screen may fail at its limit under load, where before it would not. T5.7 looks for this.
- **A leader's task may outlive its readers** by up to 15 s if the cancel setting fails, holding a place. Bounded, not removed.
- **Step 10's library is unproven here.** T3.7 is a day's trial, not a proof.
- **The demo's traffic is not a customer's.** T5.7 and T11.6 say so, and they may still pass on what would fail in production.
- **F28 and F19 are moving documents.** T5.4 follows what they say when they merge.

## What these tests cannot prove

| What | Who checks, how | Recorded | Unproven at release |
|---|---|---|---|
| A real screen reader on the view and builder | The founder or a named person, in T11.7, naming the reader and browser | `status.md`. **A release condition** | Other readers |
| A banner readable "from across a room" | The founder, on a real wall display or large screen | `status.md` | Other rooms and screens |
| A wall display across its weekly sign-out | The founder, one week on a viewer account | `status.md` | Whether anyone accepts it |
| Real phone touch | The founder, on one iPhone and one Android, in T4.10 and T6.8 | `status.md` | Other devices |
| Hostile values showing as visible escapes on phone Safari (AC74) | The founder, on one iPhone, in T3.3 and T5.6a. `tools/e2e/playwright.config.ts:62-68` runs Desktop Chrome only | `status.md`, with device and date | Other iOS versions |
| Customer-scale data | **Nobody before the first customer.** T5.7 is the demo, and says so | The report | Real volume, real cardinality, real queries |
| Findings' accuracy on a chart | T5.7 compares one window | The report | Any general tolerance |
| Buyer value of the sales line | Founder decision 1's conversations | `spec.md` | Everything, until customers buy |
| A pasted secret in backups | No test: purge does not reach them, and the docs say so | Setup page | By design |

## Questions

Decisions are in `spec.md`. The founder's were answered on 2026-11-02 (1 and 4 approved as recommended; 3 is D61).

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | What the buyer answers change. **Rule approved 2026-11-02;** applied to the round's answers | Step 2 | `spec.md`, *Founder decisions needed* |
| 3 | Do read limits rise with the plan? | What sales may say | `spec.md`, *Founder decisions needed* |
| 4 | The positioning and sales lines. **Approved 2026-11-02;** caps published only after T5.7's Oracle rerun | T11.5's public docs | `spec.md`, *Founder decisions needed* |

Answered 2026-11-02, not asked again: whether to hold buyer conversations is the foundation's founder decision 8 (spec D60); moving `/` as soon as F6 merges is spec D58; what "a cap too low to be useful" means is spec D59.

## Gates

- [x] Specification approval: 2026-11-02
- [x] Design approval: 2026-11-02
- [x] **Plan approval:** 2026-11-02
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design, for customer-facing work
- [ ] First demo gate (T11.4)
- [ ] Measurement gate (T5.7, T11.6)
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

- **Whole-change runs, once at the end of each step:** API unit tests, web `vitest`, type check and lint, read for their counts.
- **Live suites**, with the stack up: `test_dashboards_live.py`, `test_dashboard_panels_live.py`, `test_dashboard_panels_seams.py`, `test_dashboards_real_tokens_live.py`, `test_deletion_live.py`, `test_services_live.py`, `test_metric_series_live.py`, and the scheduler's `test_dashboards_expiry_live.py`.
- **Structural:** `test_ratelimit_coverage.py`, `test_authorization_coverage.py`, `test_read_surface_structure.py`, `test_tenant_chokepoint_structure.py`, `test_dashboards_have_no_ai.py`, `test_dashboard_outbound_imports.py`, `route-manifest.test.ts`.
- **Regression runs, required, because F20 changes what they rest on:** `test_alert_messages.py` and `test_alert_links.py` (the scheduler's link), `test_detection_has_no_ai.py` (the walker), `test_detection_store.py` and `test_detection_store_live.py` (`READ_SETTINGS` unchanged), `test_detection_status_api_live.py` (F6's findings read), `test_ratelimit_callers.py`, and the scheduler's existing `sweep_expired` tests (`test_leader_live.py`).
- **Browser:** **`just check` and CI do not run Playwright** (`justfile:279-281`, `ci.yml:77-78`), and adding a job is not this feature's decision (D51). So every browser-only half has a named spec under `tools/e2e/tests/`, `just e2e` is run at each step's end and before release, and results go in `status.md`. **A later change can regress these unseen between those points.** Specs: `dashboards-picker`, `-keyboard`, `-chart`, `-marks`, `-hostile-names`, `-link`, `-phone`, `-display`, `-drag`. Screenshots at phone and full width.
- **Measurement:** T5.7's and T11.6's reports and T4.10's trace, committed. Exit code alone is not evidence (AGENTS rule 12).
- **AI off:** the suite runs with `scheduler-insight` absent.

**Unrun today:** all of it. Nothing exists to run.

## Rollout and rollback

Pre-deployment, so there is no customer to protect. The real risks:

- **Step 1 ships and nobody judges it**, or the feed proves a worse first screen than Overview for a new user. It is one route to reverse, plus one scheduler link and the moved plots.
- **The first dashboard format ships to a customer.** After that, `schema_version` is forever. Before the first customer, the format can still change freely, and should be looked at again after Step 9.
- **Rollback:** the migration refuses its downgrade ("roll forward only", founder 2026-11-09); rolling back is a restore. The revert and removes `dashboards` from the sweep line, in the same revert. The API's DSN returns to `nexovue_reader` by one line, and the new account can stay unused. Steps 2 to 10 add routes and screens that nothing else depends on, with these exceptions: `scope["state"]` gains two keys, proved by `test_ratelimit_callers.py` being unchanged; `lifespan.py` loses an import; and `headline.ts` and `overview-plots.ts` have moved.

## Outcome

**Step 2 built and validated, 2026-11-08**, on `feature/phase-2`. A saved dashboard is read through one charged batch: `EDIT_DASHBOARDS` and `PURGE_DASHBOARDS`; `dashboards` and `dashboard_versions` (migration 0056, revising 0053 until re-chained); the validator and compiler with the spec's allowed values for `project_trend`, `service_trend` and `note`; `ratelimit/charge.py` charging each later scan in the middleware's own buckets; the process caps, the shared reads and `bounded_by`; `/dashboards` CRUD, templates (*Start from Overview*, partial until Step 5) and the Postgres-only context; `/dashboard-panels/read`; and the console's list, title-and-save dialog and read view. Built as written, with three layout corrections recorded in `status.md` (flat feature folder, the view test at the app layer, T2.17's shared half already done by F19). ADR-031 and ADR-032 filed. One open decision: the migration's downgrade. See `status.md` for counts and what was not run.

**Step 1 built and validated, 2026-11-02** (branch `wt/f20`, uncommitted; the coordinator reviews and commits). `/` serves the Findings feed, Overview is at `/overview`, the `/findings` list route is gone, the feed has FD15's real empty state and draws Overview's three charts from the same `/services/overview` answer, the scheduler's project notice links to `{base}/`, and `console-routes.json` now holds every console route for the scheduler's new `test_alert_links.py`. T1.8's comments corrected, no behaviour change. Steps 2 to 11 not started; see `status.md` for validation counts and residual risks.
