# Status: dashboards (F20)

## Lifecycle status

**In development.** The founder approved the specification, design and plan on 2026-11-02. F6 merged (PR #24), and **plan Step 1 — the front door moves to the Findings feed — is on `feature/phase-2`, 2026-11-02** (commit 44795cb). **Plan Step 2 — one saved dashboard, read through a charged batch — is on `feature/phase-2`** (migration 0056, ADR-031, ADR-032). **Plan Step 3 — the builder — is on `feature/phase-2`** (2026-11-09). Steps 4 to 11 are not started.


## Current task

**Step 3 is on `feature/phase-2` (2026-11-09).** T3.1 to T3.8 are built; T3.3 is left unticked only for the phone hand check, which is the founder's after Phase 2. No migration and no new ADR. Three open decisions below, none blocking the commit.


## Completed

- **2026-11-09. Plan Step 3 built (T3.1 to T3.8).** What changed, read from the diff:
  - **T3.1.** `POST /api/v1/dashboard-panels/preview` (`dashboardpanels/router.py`), gated by `MayEditDashboards`, so a viewer and a support view are refused (AC8) and an API token reaches it no more than any dashboard route. Its body (`PreviewBody` in `dashboardpanels/schemas.py`) is the read's address plus the draft, `extra="forbid"`, and the draft is a strict `Definition`, so FR7's refusal applies to a draft as to a save. `dashboardpanels/service.py` now has one path for both reads: `read_panels` reads the saved row and stamps "last opened"; `preview_panels` runs `compile_for_save` first, reads no saved row and stamps nothing; both share `_scan_and_assemble` (the scans, the charge, the deadline from arrival) and the log line, which now carries `draft`. A draft is read back through `parse_stored`, as a saved definition is, so the two cannot differ. Registered in `POLICY_OF`, the routing test, the real-token test and AC8's structural case.
  - **`GET /dashboards/{id}` gains `last_viewed_at`** (`dashboards/schemas.py`, `service._response`), for delete's "last opened 2 hours ago" (FR25). Stored already; no migration.
  - **T3.2.** New `features/dashboards/draft.ts`: the pure reducer (add, replace, move, resize, height, duplicate, remove, undo, set, settings, reset), `isDirty` comparing key-sorted JSON with what is stored, `worstCaseReads` (the save's worst case, as `compile.py` counts it), `announce` (the live region's sentence for each change), and the caps `MAX_PANELS`, `MAX_SCANS_PER_READ` and `MAX_LINKS`, held equal to `definition.py` and `policy.py` by a test reading their source.
  - **T3.3.** New `SideSheet.tsx` (a native `<dialog>`, `lg:w-[clamp(28rem,40vw,60rem)]`, full screen below `lg`, Escape through the platform's `cancel`, focus back to its opener) and `PanelPicker.tsx` (the kinds grouped by question; services from `GET /services` in a native select, each label through `visibleEscapes` and each value raw; a "Type a name" field showing what it reads as; a warning when a chosen name holds hidden characters; Apply reading that one panel through `/preview`, never per keystroke; a note drawn from its own text with no read; the six states). `shared/api-client.ts` gains `isInvalid` (a 422), because only that module may read a status.
  - **T3.4, T3.5, T3.8.** New `DashboardEditScreen.tsx` with `DashboardEditScreen` and `NewDashboardScreen`, both inside `Permitted`; new `PanelToolbar.tsx` (the WAI-ARIA toolbar: one tab stop, arrows, Home and End, each action named for its panel, `aria-disabled` at the ends so focus stays, Up and Down with one menu per panel below `lg`, 44 px below `md`); new `LinksEditor.tsx` (`EditableRows`, the host each address opens shown beside it); new `grid-spans.ts`, now used by both the read view and the builder so the builder shows the real layout (FR49). The builder reads the stored draft once on opening and then only on Apply; focus follows `EditableRows`' rule; one polite live region says every change; `useUnsavedChanges` warns only while something differs from what is stored; a 409 keeps the draft on screen with the API's words, "Reload it" and "Save as a copy"; a 422's fields are listed in one summary; title, picker and cap checks are said before anything is sent. Routes `/dashboards/new` (`?template=`) and `/dashboards/$dashboardId/edit` in `router.tsx`, `console-routes.json` and the manifest test's list of screens reached by following something. The builder joins `unsaved-changes-coverage.test.ts`'s guarded forms.
  - **T3.6.** New `DashboardActions.tsx`, mounted by the builder: delete through `ConfirmPanel`, "*Checkout on-call*, 12 panels, last opened 2 hr. ago. It can be restored for 30 days.", with "Export it first" (the document alone, no ids, owner or version, FR42) and FR62's export sentence; focus returns to the button when kept.
  - **The read view and list.** `DashboardScreen.tsx` offers "Edit" to an editor only (AC7, AC105) and has the page's one live region, "Dashboard loaded, N of M panels read" (AC143); the stale strip is no longer a second live region. `DashboardListScreen.tsx` offers each starting point and "New dashboard" as links into the builder (FR39, AC38). **`TitleDialog.tsx` is deleted:** it was the templates-only fallback's save dialog (plan, *The fallback's own tasks*), and the founder chose the full builder (foundation item 27); TF.1's path would need it back.
  - **T3.7.** The drag trial, below (*Drag trial*). Nothing from it ships.
  - **Not built here, by the plan:** versions, duplicate and restore (Step 7); the other templates and import (Step 9); drag (Step 10); the project-clock declaration to the top bar (T6.7).
- **2026-11-08. Plan Step 2 built (T2.1 to T2.19)**, under the founder's decision of 2026-11-08 (foundation item 27): the buyer round no longer gates it, and the full builder's path is built. What changed, read from the diff:
  - **T2.1.** `EDIT_DASHBOARDS` (owner, admin, member) and `PURGE_DASHBOARDS` (owner, admin) in `auth/permissions.py`, `MayEditDashboards` and `MayPurgeDashboards` in `auth/policy.py`, and the copies: `test_policy.py`, `test_auth_routes.py`, the console's `shared/identity.ts`, `identity.test-util.ts`, `MembersScreen.test.ts`, and FR1's table in `team-and-api-access/spec.md`.
  - **T2.2.** `Dashboard` and `DashboardVersion` in `data/postgres/models.py`; migration **0056** (`0056_dashboards.py`, revising 0055, F22's connections). `dashboards` is deleted directly with the organization, before `users`, and `dashboard_versions` cascades from it (`organizations/tables.py`, `organizations/queries.delete_dashboards`). Partial unique index on `(org_id, lower(title))` for live rows, `(org_id, last_viewed_at)`, GIN on `service_names`, `UNIQUE (org_id, id)` so versions key on both. The downgrade refuses ("roll forward only"), by `database.md`, which conflicts with the design: see *Open decisions*.
  - **T2.3.** New `ratelimit/charge.py`: `Counted` moved there; the middleware records the buckets it counted a request in and the request's arrival (monotonic, before any wait, limits on or off) in `scope["state"]`; `ScanCharge` lets the first scan ride on the request's place and charges each later one in every bucket by the middleware's own keys; absent means limits off. `test_ratelimit_callers.py` unchanged.
  - **T2.4.** `/dashboard-panels` joins `TELEMETRY_PATHS`; `MAX_SCANS_PER_READ` 40, `REFRESH_FLOOR_SECONDS` 30, `LARGEST_SUPPORTED_API_INSTANCES` 2, `PANEL_READ_DEADLINE_SECONDS` 20, `PANEL_READ_MARGIN_SECONDS` 5, `PANEL_READ_CLIENT_TIMEOUT_SECONDS` 25 (all the spec's guesses).
  - **T2.5.** New `dashboards/definition.py` (one union on `kind`, `extra="forbid"`, the spec's allowed values for `project_trend`, `service_trend` and `note`, 30 panels, 64 KiB, `small` refused on charts, control and invisible characters refused in titles, labels and panel titles, NUL refused everywhere, `https`-only links with no person and no credential-shaped query key, the forgiving `parse_stored`), `dashboards/compile.py` (scan keys, the picker as one service apart from named ones, the worst case refused past the cap naming the count and the panels), `dashboards/ranges.py` (one list of range ids, and a live window's end rounded up to the next minute).
  - **T2.6, T2.7, T2.15.** New `dashboards/{router,service,queries,schemas,templates}.py`: list from columns; create (caps of 1,000 live and 2,000 rows under a per-organization advisory lock; unique title); read; replace with the version lock (a 409 naming who saved), no version and no entry for a save changing nothing, the replaced version kept when the title or definition changes, and trimming past the newest 20 and older than 30 days under a ceiling of 200; soft delete; `GET /dashboards/templates` with *Start from Overview* (three `project_trend` panels until Step 5). Audit `dashboard.created`, `.updated`, `.deleted` with title, previous title, panel count, version, the definition's SHA-256 and link hosts added and removed; `.restored` and `.purged` named for Steps 7. Problem types `dashboard-not-found` and `project-not-found` in `errors/problems.py`.
  - **T2.8.** `services/service.py`: `project_totals` and `project_trend`, one statement each, with `project_overview` a thin composer; its route and tests unchanged.
  - **T2.9.** `TenantScopedRepository.bounded_by(seconds)` in `data/clickhouse/base.py`: a copy (no new construction site) sending `lowered_read_settings` and `cancel_http_readonly_queries_on_client_close`; `READ_SETTINGS` and `DETECTION_READ_SETTINGS` unchanged. `server_error_code` reads the driver's code there, since only that package may import the driver.
  - **T2.10, T2.11.** New `dashboardpanels/caps.py` (5 and 2 per process, 3 and 1 per organization where more than one can exist, a 5 s wait) and `dashboardpanels/shared_reads.py` (kept answers for 30 s keyed by tenant, project, reader, arguments and window; joined reads; a leader takes its place and its charge before creating the in-flight entry; a task-owned statement with a fixed 15 s limit, cancelled when no reader is left; a joiner whose leader failed tries once more; 100,000 rows, 25,000 per organization, 2,000 an answer; ClickHouse 159 → `timed_out`, 202 → `refused`, anything else → `failed`).
  - **T2.12, T2.13.** New `dashboardpanels/{router,service,scans,schemas}.py`: `GET /api/v1/dashboard-panels/read` in the design's sequence (Postgres first, stamp, commit, compile, layout-order scans 4 at once under the deadline from arrival, nothing started with under 1 s left or after the client has gone, assemble), and its log line. Both routers in `main.py`; the caps and kept answers built once with the app (`app.state.dashboard_reads`). Panel times spelled as every telemetry screen spells them (`wire.IsoTimestamp`).
  - **T2.14.** `GET /dashboards/{id}/context`: project, clock, plan retention (null self-hosted), window, detection's status, `ever_reported`, `last_seen_at`, findings in the window and open counts. New `detection/queries.project_findings_in_window` (newest first, 200, sharing `service_findings`' overlap predicate, which is otherwise unchanged) and `open_findings_by_service`.
  - **T2.16, T2.17, T2.19.** A structural rule in `test_read_surface_structure.py` (every F20 handler names `CurrentIdentity`, `MayEditDashboards` or `MayPurgeDashboards`, and none a `CurrentReader`); `test_dashboards_real_tokens_live.py`; `test_dashboards_have_no_ai.py` with its control (F19 had already done the `lifespan.py` Protocol and `import_walk.py`); `test_dashboard_outbound_imports.py`.
  - **T2.18.** `/dashboards` and `/dashboards/$dashboardId` in `router.tsx`, *Dashboards* in Home (`route-manifest.ts`), both in `console-routes.json`. `app/query-client.ts` honours a query's `meta.ownWords` (`unavailable`, `rate-limited`): no toast for what the screen says itself; `shared/api-client.ts` gains `isRateLimited`. `headline.ts` and its test moved to `shared/detection/`, with `ProjectTotals`, `CheckCounts`, `DetectionStatus` and `DetectionState`; `findings/api.ts` imports and re-exports them. New `shared/format/visible-escapes.ts`. New `features/dashboards/`: `api.ts` (the batch with `timeoutMs` 25 s, `retry: false`, the dashboard's own refresh), `DashboardListScreen.tsx` (one action, *Start from Overview*), `TitleDialog.tsx`, `DashboardScreen.tsx` (header with the project and its clock, links with their host, detection's line, "N of M panels could not be read", one page line for an outage, a whole-request 429 and the 25 s timeout, each panel a `<section>` named by its `<h2>`), `layout-rows.ts`, `panel-words.ts`, `kept-readings.ts` (FR31's one interval under a strip), `TimePanel.tsx`, `NotePanel.tsx`, `PanelFrame.tsx`.
  - **ADRs filed:** [ADR-031](../../decisions/ADR-031-a-dashboard-is-a-checked-document-of-fixed-panel-kinds.md) and [ADR-032](../../decisions/ADR-032-a-batch-read-is-charged-by-the-scan.md), indexed in `docs/decisions/README.md`. The third proposed ADR, the API's own ClickHouse account, belongs to Step 5 (T5.6b) and is not filed yet. The coordinator renumbers if ADR-031 or ADR-032 collides.
- **2026-11-08. T2.0 written** ([`docs/research/f20-board-mapping-2026-11.md`](../../research/f20-board-mapping-2026-11.md)): ten sample boards, 143 panels (matches the plan); 7 map to a panel kind, 68 partly, 68 not offered; 40 of the 75 that map or partly map need Step 8's `metric_series`. Internal research, not yet reviewed by the founder.
- **2026-11-02. Plan Step 1 built (T1.1 to T1.9).** What changed, read from the diff:
  - `apps/web/src/app/router.tsx`: `/` serves `FindingsScreen`; `overviewRoute` at `/overview`; the `/findings` list route deleted; the stale OD2 comment rewritten; the 404 screen says "Back to Findings" (T1.1, T1.4).
  - `apps/web/src/app/routes/route-manifest.ts`: Home offers Findings `/`, Overview `/overview`, Alerts (T1.2).
  - New checked-in `apps/web/src/app/routes/console-routes.json`, held to the router by `route-manifest.test.ts`, and read by the scheduler's new `apps/scheduler/tests/test_alert_links.py`, which composes an incident, batch, finding and project message and matches every `link` and `manage_link` against it (T1.6, FR43, AC54).
  - `apps/web/src/app/layout/Sidebar.tsx`: Findings active on `/` and on `/findings/$findingId`; proved by the new `Sidebar.test.ts`, a real render of the app router (T1.4, AC55).
  - `apps/web/src/features/findings/FindingScreen.tsx`: back link to `/` (T1.3).
  - `apps/scheduler/.../alerts/messages.py:378`: the project notice links to `{base}/` (T1.5, AC2).
  - `overview-plots.ts` moved to `apps/web/src/shared/overview-plots.ts` with `OverviewPoint` declared there; both features' `api.ts` import it from `shared/`; a search confirms `shared/` imports nothing from `@/features` (T1.7).
  - FD15's empty state: `headline.ts` treats `service_count === 0` as "Nothing has reported in this window" (after a down check's line, which a probe proved); the feed hides the zero readings and charts, links the setup docs and says to widen the window. With telemetry and no findings, the feed draws Overview's three charts from the same `/services/overview` answer (`points`, no extra scan) and links to `/overview` (T1.7, FR3, AC3).
  - T1.8: the four false "a fifth read is refused" comments corrected to the measured truth (cap 8 per process, waits, 4 at two processes; the gating stays). Comments only, no behaviour change.
  - T1.9: `feature-catalog.md` and `console-redesign/wireframes-dashboard.md` now say the front door moved, dated.
- **2026-11-02.** Research written ([`research.md`](research.md)).
- **2026-11-02.** Read the foundation, the Phase 2 plan, the workflow, the F6 documents and the code they cite, on `feature/anomaly-detection`.
- **2026-11-02.** Wrote the specification, design and plan from that reading.
- **2026-11-02.** Eight persona reviewers read them. Spec and design were changed after a first round, whose record was never written here. A second round reported the 122 findings under *Reviews*, each then checked against the code or the source. The plan was rewritten from the current spec and design.
- **2026-11-02.** A follow-up review reported 14 more findings. Each was checked and led to a change, recorded under *Reviews*, *Follow-up review*.
- **What this feature found that research did not have:**
  1. The rate-limit test judges a `router.py` as a whole, so the batch read needs its own package, and its file must be named `router.py`.
  2. A colon route would have made router order load-bearing, so the batch has its own prefix, `/dashboard-panels` (D41).
  3. The API has 8 ClickHouse connections shared by every tenant (`settings.py:209`), so caps must sit below that, and caps in the API alone do not protect alerts, so the API gets its own bounded read account (D45).
  4. A one-project API token reading organization-wide definitions would learn other projects' service names, so tokens get no dashboard route (overrules research R14).
  5. "Start from this service" without a service picker makes a copy per service, the sprawl this feature avoids (overrules research R10).
  6. Deploy marks use F28's `markers_in_window`, which includes versions only seen in traffic, and these can be planted, so they are drawn differently (D14).
  7. `HEATMAP_SQL` has the same suspected fault as `HISTOGRAM_SQL` (read only).
  8. A shared request session cannot serve concurrent scans, so Postgres reads come first, then a commit.

## Next action

**Step 3 (2026-11-09):** the coordinator reviews the diff, runs `just check` and commits. Then the founder's judging step for Step 3 in a browser (*Validation*, *Step 3*), the three open decisions below, and Step 4. The phone hand check of the picker (AC74) is the founder's after Phase 2.

Still owed from Step 2: the founder's judging step for Step 2 in a browser (*Validation*, *Step 2*). The third proposed ADR is filed with T5.6b.


## Open decisions

**2. Does T3.7's trial count as passed, so Step 10 keeps drag? (2026-11-09, Step 3.)** The plan's stop rule drops Step 10's first task if drag "cannot reorder mixed widths reliably". Measured (below): the order a drop leaves was right in 80 of 80 pointer drags, but the sortable library's own preview, which slides the other panels as you drag, overlapped panels in 21 of 40 drags with mixed widths. With the other panels held still until the drop, 0 of 40 overlapped and the order was still right in 80 of 80. dnd-kit's keyboard sensor moves by screen position, not by one place (a panel moved right from first landed fifth), so it must not be used: the toolbar stays the keyboard's way. **Recommendation: pass, on three conditions for T10.1:** other panels hold still and the drop target is marked, no keyboard sensor, and a guard against the page scrolling sideways when a dragged panel runs past the edge (seen in 8 of 40 drags, not fixed in the trial). Options: (a) pass on those conditions; (b) fail, and drop T10.1, keeping the buttons. A product judgement, so the founder's.

**3. Which clock a new dashboard's builder draws on (2026-11-09, Step 3, T3.3).** FR46 puts every time on a dashboard on the project's clock. A saved dashboard's builder reads it from the panel context (`/dashboards/{id}/context`); a new dashboard has no id, and the project list (`GET /projects`) carries no time zone. So a new dashboard's previews draw on the browser's clock, as every other screen does today. **Recommendation:** add `timezone` to the project list's summary (one stored column, Postgres only), with T6.7, which declares the project's clock to the top bar anyway. Options: (a) that; (b) have `/preview` answer with the project's zone; (c) accept the browser's clock in the builder until a dashboard is saved.

**4. The builder reads the stored draft once on opening (2026-11-09, Step 3, T3.4).** The design draws "(live panel)" in the builder's grid and says Apply reads one panel "once per Apply and never per keystroke"; it does not say whether opening the builder reads. Built: one `/preview` of the stored definition on opening (one charged batch, the same as opening the dashboard, and free within 30 s of a view through the kept answers), then nothing until Apply. A move or resize reads nothing, and a panel changed without Apply says so. **Recommendation: keep.** The alternative, no read on opening, shows every panel as words until each is applied.


**Answered by the founder, 2026-11-09: refuse, as built; the design's and plan's lines corrected.** **1. Does migration 0056's downgrade drop the two tables, or refuse? (2026-11-08, Step 2, T2.2.)** The approved design says "Downgrade drops both" (`design.md`, *Data and migration impact*) and the plan's rollback says "the migration drops two tables" (`plan.md`, *Rollout and rollback*). `docs/engineering/database.md` says "A downgrade that would drop records that exist nowhere else raises 'roll forward only' rather than dropping them", and the three newest migrations follow it (`0051`, `0052`, `0053`; `apps/api/tests/test_migration_rules.py:35`). Dashboards and their versions are what people built and exist nowhere else. **Built as the standard says** (`apps/api/migrations/versions/0056_dashboards.py`, `downgrade()` raises; `0056` added to `ROLL_FORWARD_ONLY`), because a standard owns what is correct and the change back is three lines. Options: (a) keep roll-forward-only and correct the design and plan's rollback lines; (b) let the downgrade drop both, since nexovue is pre-deployment and no dashboard exists yet, and remove `0056` from `ROLL_FORWARD_ONLY`. **Recommendation: (a)**, because the rule exists for the first customer's day, and the rollback the plan needs is a revert of the code, which leaves two unused tables harmless. Not a product decision; the coordinator or the founder decides.

**None other for the founder.** The spec's founder decisions 1 and 4 were approved on 2026-11-02, as recommended (*Founder answers, 2026-11-02*, below); 3 is D61. The builder rule is applied to the buyer round's answers. The authors' other decisions (D1 to D61, `spec.md`) stand unless the founder overrules them.

## Own questions answered, 2026-11-02

Every open question in `spec.md`, `design.md`, `plan.md`, `research.md` and this file was checked against the code and, where outside facts mattered, the web.

- **Move `/` to the feed ahead of the stage order (was founder decision 2): decided, D58.** Yes, as soon as F6 merges, as Step 1 alone. It is the timing of FD15, and the code names the trigger itself (`apps/web/src/app/router.tsx:236`: Overview holds `/` "until the insight engine exists"). Pre-deployment, one route, reversible. `phase-2-plan.md` Stage 6 and the design's *Specification impact* now say so, Proposed.
- **Buyer conversations before Step 2 (was founder decision 1, first half): settled by the foundation's founder decision 8.** One shared round, before F28's Step 1b, carrying F20's questions. Step 2's precondition is rewritten (D60). The second half, what the answers change, stays the founder's as decision 1.
- **"A cap too low to be useful comes back to the founder" (plan T5.7): decided, D59.** Useful means every template reads with no refused panel for one person, and NFR12 passes. Only an NFR12 that cannot pass at any cap goes to the founder.
- **Read limits by plan (founder decision 3): first left with the founder; decided in review as D61 (see *Review* below).** Web research on 2026-11-02 found no rival publishing per-plan dashboard query limits (Grafana Cloud, SigNoz Cloud); sources in `spec.md`.
- **Positioning line "for the founder to approve" (spec, *What this brings*): raised as founder decision 4.** It was a founder question without a number.
- **Not F20's:** the test server's price (plan T5.7) is `phase-2-plan.md`'s open question 2. The screen-reader check "by the founder or a named person" (plan, *What these tests cannot prove*) needs no answer now: the founder does it unless he names someone.
- **`research.md`'s two founder items** are marked superseded, with pointers.

### Review, 2026-11-02

A skeptical review (architect, developer, UX, sales, marketing, customer, security, QA) checked each answer above against the code and the foundation. Every code citation held (`router.tsx:236`, `policy.py:15`, `policy.py:303-311`, `anomaly-detection/spec.md:216-220`). What changed:

- **Wrong reference fixed.** The answers called the buyer round "FD8". In `phase-2-foundation.md`, FD8 is the table row "who may ask the AI"; the buyer round is the foundation's *Founder decisions*, item 8. Every F20 reference now says "the foundation's founder decision 8".
- **Founder decision 3 is decided, as D61.** Equal read limits on every plan is not a new pricing choice: FD20 (no plan gating) and foundation section 10 (limits protect the system, not upsells) already settle it, and BR8 repeats it. Only a future choice to sell higher limits would be the founder's. The founder list drops to 1 and 4.
- **Founder decision 4 now covers the price reassurance.** "Dashboards cost nothing extra, on any plan, and we never price by person" is a public promise about price, so it joins the two lines the founder approves. It also notes that a templates-only outcome forbids "build your own dashboards".
- **Founder decision 1's rule made countable.** "At least half" is now 3 of 5 or 4 of 8, and counts only buyers whose answer rests on a dashboard they built and use.
- **Held up unchanged:** D58 (`/` moves at F6's merge; timing of FD15, reversible, pre-deployment), D59 (the stated test for a useful cap; failing NFR12 at any cap still goes to the founder, since it changes what buyers can be told and may need a larger server), D60, and the test server's price staying `phase-2-plan.md`'s open question 2. The screen-reader check defaulting to the founder names nobody new, so it needs no decision.
- **Web research:** not re-run in review. D61 rests on the foundation's recorded decisions, not on rivals; the earlier searches stay as supporting evidence only.

## Blocked

- **Founder decision, 2026-11-08:** Step 2 onward no longer waits on the buyer round (foundation item 27); the full builder is built, not templates only.
- **Step 3 is built** and waits only on the coordinator's review and commit (2026-11-09).
- **Step 5 onward** waits on F28.

## Reviews

Eight personas reviewed `spec.md`, `design.md` and `plan.md`. Each finding was checked against the code or the vendor's page before anything changed. **Nobody re-reviewed the result.** The verdicts below are what the reviewers said before these changes, not a judgement of the documents as they now stand.

**Result: 122 findings. 121 led to a change, and 1 was rejected whole. 21 had a part rejected, listed below.** A finding counts as fixed when the documents now answer it. That means a requirement, a design and a test are written. It does not mean anything works, since nothing is built and nothing was run.

Reviewers' line numbers, including the F28 citations in the table below, point at an earlier revision and have moved. Where a claim was about the earlier text of these documents, it was checked against that text before the change.

### Verdicts

| Persona | Verdict at review | Findings | Blockers |
|---|---|---|---|
| Architect | Not ready | 15 | 1 |
| Developer | Not ready | 18 | 2 |
| UX | Not ready | 23 | 3 |
| Sales | Not ready | 10 | 2 |
| Marketing | Ready with fixes | 12 | 0 |
| Customer | Not ready | 17 | 1 |
| Security | Ready with fixes | 9 | 0 |
| QA | Not ready | 18 | 1 |

### Conflicts between personas, and how they were decided

The task named a decision rule without stating it, and none was found in the repository under that name. These rows use the rule F28's review wrote down in the same position, drawn from the run's standing rules: the correct end state first, then what a tired engineer can trust, then cost. The founder may overrule any row.

| Conflict | Decided | Why |
|---|---|---|
| Fix the time budget by `2 x 5 s` and `timeoutMs` 30 (Architect, Developer); share one deadline in the middleware (Architect's second); count from arrival (Developer's second) | Count from arrival, `timeoutMs` 25 (D25) | Any wait, Postgres's too, then shortens scans instead of overrunning, and a sum of constants can never drift. An on-call page answers sooner |
| Keep metric series after templates, and correct the spec to "Steps 1, 2, 4 to 8" (Marketing); settle one order (Architect, Developer); put metric series first (Sales) | Spec's order: 8 metric series, 9 templates (D23, D40) | D23's reason holds. The founder's fallback sentence and the buyer table then become true, and it is restated by name too |
| Signed deploys only (Security's first option); `markers_in_window` (Architect, Developer, QA) | `markers_in_window`, with `seen` drawn and worded differently (D14) | Security's second option. One install must draw one set of marks, and `basis` carries the risk to the screen |
| Share of the cap: fixed 3 of 5, 1 of 2 (Architect, Security); 2 of 5 only when others wait (Sales) | Fixed, and whole cap on a single-organization install (D26) | A running scan cannot be taken back, so "when others wait" still refuses the other tenant. Self-hosted loses nothing |
| Memory bound in rows (Architect); in bytes, 64 MB (Security) | Rows: 100,000, 25,000 per organization, 2,000 an answer (D42) | Rows can be counted exactly without guessing object size. Security's per-organization share is kept. T5.7 reports real memory |
| Slots kept and `counted` passed to the route (Developer); slots removed (Architect) | No slots for later scans; `counted` read from `scope["state"]` for quota (D43) | The two agree once slots go, and it is the smaller design. BR6 holds, since quota charges each scan |
| Datalist tested early, with a listbox as a fallback (QA); select plus typed field (UX); a control showing an escaped label (Security) | Select plus typed field, tried in browsers at T3.3 (D46) | The HTML standard already answers what a datalist does, a custom listbox is what `SelectField.tsx:4-9` refuses, and QA's test stays |
| `small` height raised to 132 px (UX's second option); `small` for numbers and notes only (UX's first) | Numbers and notes only | One rule, and no change to `medium` |
| Feed with charts: always when there are no findings (Sales); only until detection first judges (Customer) | Whenever the feed is empty and telemetry exists | `SummaryState`'s own comment says the summary is the content when the feed is empty |
| CI job for browser tests, or named hand-run points (QA's either) | Named points, in `status.md` (D51) | Stated plainly, with the gap named. A CI job is a decision about CI |
| Bulk export and all-project token (Customer); state it plainly (Sales) | State it plainly, and put it to buyers (D9, D39) | No buyer has asked yet, and 1,000 reads would need a server route. It may come back |
| Scope of the top-bar clock: browser's clock, said so (earlier FR46); project's clock (UX, Customer) | Project's clock while mounted (FR46) | One screen, one clock, and the phone's only window control sits there |

### Rejected whole

| Finding | Why | What was done instead |
|---|---|---|
| Customer 13: add `show_normal` on `service_trend`, served from `detection_baselines` through the Postgres-only context | The source cannot do it. `detection_baselines` keeps one hour (`detection/baselines.py:117,142-146,1101`), which the reviewer said it had not read. So no past window has a band | D54: not in this version. The buyer table says so, the conversations ask, and a later panel can read `detection_readings`, for one scan per service. **It remains a real gap, and possibly the best difference F20 could offer** |

### Parts of findings rejected

Everything else in each of these findings was fixed.

| Finding | Part rejected | Why |
|---|---|---|
| Architect 7 | `ceil` of seconds remaining as the statement limit | Superseded by Architect 5's fixed limit, which needs no rounding. The under-1-s rule and the error mapping stay |
| Developer 8 | `timeoutMs` 30 | Conflict above |
| Developer 13 | Delete `findings_in_window` altogether | A separate read must exist, or F6's screen changes (Developer 14, QA 5). It is `project_findings_in_window`, in `detection/queries.py` |
| UX 16 | Raising `small` to 132 px | Conflict above |
| Sales 5 | "When others are waiting" | Conflict above |
| Marketing 1 | Rewrite as "Steps 1, 2, 4 to 8" | Conflict above |
| Marketing 3 | Datadog can filter to faulty changes flagged by Watchdog | Not on the pages read on 2026-11-02. "Automatic Faulty Deployment Detection" appears only as further reading. Listed as not confirmed |
| Marketing 7 | SigNoz's Terraform supports Community Edition from v0.85.0 | The page read says `signoz_dashboard` needs v0.135.0 or newer, is tagged Self-Host, and does not name Community Edition. Listed as unconfirmed, so "a free-edition gap" is not stated |
| Marketing 8 | SigNoz's LGTM guide | Not read. Datadog's and New Relic's were |
| Marketing 9 | Grafana usage insights from v7 docs | Not re-read. The downgrade stands on Datadog alone |
| Customer 2 | Fixed hourly rollup | D50: a writer path and storage nobody has measured. T5.7 tests 30 and 90 days first |
| Customer 3 | `histogram_quantile_trend` now | D49: its read has a suspected fault until T8.1. The gap is in the buyer table |
| Customer 4 | Node picker | D7: no other variable. A builder hint points to `top_nodes_trend` |
| Customer 6 | A new alert on `settled_minute`; lower `priority` | The alert exists (`nexovue.yml:102`). `priority` needs the scheduler's reads to carry it and is untested (D45) |
| Customer 9 | Shading incidents; a dashboard field on alert rules | D52: a second colour rule, a change to F21's schema |
| Customer 11 | All-project token reads; Export all | D9, D39, conflict above. T2.0's mapping and the buyer rows are added |
| Customer 17 | The telemetry quota as a setting | `policy.py:14`: quotas are product policy. The scan cap and floor became settings |
| Security 4 | Keeping signed deploys only | Conflict above |
| Security 9 | A byte budget | Conflict above |
| QA 6 | A CI job | Conflict above |
| QA 18 | Keep datalist if it tests well | Conflict above |

### What was changed, finding by finding

"Verified" means the claim was read in the code or source named, in this review.

| Finding | Severity | Verified against | Changed |
|---|---|---|---|
| Architect 1: plan and status describe an older design | Blocker | Both files as they stood; `selection.ts:150-155` | Plan and status rewritten; Impact, rollback and scheduler line corrected; one step order; fallback by name and number |
| Architect 2: 10 s of slot waits | Major | `middleware/ratelimit.py:96,195-205`, `policy.py:92,99`, `settings.py:201` | D25, design's *Time budget*, FR59, AC84, T2.3 |
| Architect 3: F20 and F28 disagree | Major | F28 `design.md:496,512-515,722`, `spec.md` D29, D30 | D14, FR18, design section 9, context JSON, AC122, sales line, T5.4 |
| Architect 4: cache capped by entries | Major | `nexovue.yaml:329`, `base.py:85` | D42, design's *Sharing a read*, AC90, T5.7 reports memory |
| Architect 5: joined read undefined | Major | Earlier design text | Rules table, D42, AC86 to AC89, AC59 |
| Architect 6: nothing bounds ClickHouse | Major | `tenant_statements.py:44`, `reader.xml`, `nexovue.yaml:291,360`, `Settings.cpp` | NFR14, D45, new ADR, AC91, T5.6b, T5.7 |
| Architect 7: limits read `failed` | Major | `tenant_statements.py:75-85`, `reader.xml:29-32` | Fixed limit, under-1-s rule, error mapping, AC19, AC92 |
| Architect 8: `check_trend` reads every check | Major | `synthetics/service.py:47,67,113-132`, `queries.py:190-215` | `CHECK_TREND_SQL`, FR6, source column, AC95, T5.1c, T5.7 |
| Architect 9: project not protected | Major | `test_tenant_chokepoint_structure.py:116,540-555` | NFR2, AC26, AC85, inner `SELECT`, T4.1 |
| Architect 10: cancel setting in shared constant | Minor | `tenant_statements.py:47`, `base.py:84` | Sent by `bounded_by` only; AC93 |
| Architect 11: no share per tenant | Minor | `policy.py:99`, earlier FR13 | FR13, D26, AC94 |
| Architect 12: slots add little | Minor | `limiter.py`, earlier design | D43, FR12, AC16, `ScanCharge` |
| Architect 13: colon route | Minor | `test_ratelimit_coverage.py:30,48-60`, foundation `:422,479` | D41, spec's table, AC24, routes |
| Architect 14: list reads JSONB | Minor | Earlier routes and table | `panel_count`, `service_names`, `uses_picker`, GIN index; AC96 |
| Architect 15: cold 503 retried and toasted | Minor | `query-client.ts` | `meta`, no retry; AC61; T2.18 |
| Developer 1: plan out of date | Blocker | Counts re-run on the old plan | Plan rewritten; *Traceability* holds every FR, NFR and AC, and a script confirmed it |
| Developer 2: founder decision's steps | Blocker | Old `plan.md:171,179`, `spec.md:564` | Renumbered; decision 1 by name; dead references fixed. **The fallback check here was wrong, and is corrected under *Follow-up review*:** it needs T2.18's small dialog and the plan's TF.1 and TF.2 |
| Developer 3: Step 1 cannot ship | Major | `messages.py:378`, `test_alert_messages.py:416`, `Sidebar.tsx:69`, `router.tsx:758-759`, `headline.ts:33` | T1.4 to T1.7 |
| Developer 4: T1.6 removes waits | Major | Spec's own text, `limiter.py`, `findings/api.ts:241-244` | T1.8: comments only |
| Developer 5: Step 4 unjudgeable | Major | Old plan | T4.1, T4.2 moved ahead; log line in T2.12; viewer judging in Step 3; partial Overview stated, AC39 held |
| Developer 6: Step 2 contradicts the design | Major | `test_services.py:632`, `test_services_live.py:352`, old plan | `compile.py` in `dashboards/` at T2.5; `templates.py` at T2.6; AC21 at T2.12; `project_overview` kept thin at T2.8; commit in T2.12; structural rule at T2.16 |
| Developer 7: T2.11 fails first run | Major | `lifespan.py:21,137`; `test_detection_has_no_ai.py:37-40` | T2.17 |
| Developer 8: time budget | Major | As Architect 2 | As Architect 2 |
| Developer 9: three documents on markers | Major | F28 `design.md:722`, `spec.md` D30 | As Architect 3; F28 a precondition; C1 corrected |
| Developer 10: T5.2 not a task | Major | `infrastructure/`, `dbstats/` files | T5.1a to T5.1d, T5.2, T5.3 |
| Developer 11: tasks name no test | Major | Old plan; `route-manifest.test.ts`, `app/layout/` listing | Every task names a file and a test. `Sidebar.test.ts`, `shared/finding-spans.ts`, `DashboardListScreen.tsx`, `DataTable` in T7.2, `test_dashboards_routing.py` |
| Developer 12: measurement measures too little | Major | Spec's NFR11, NFR12; design | T5.7 rewritten with pass marks first; T11.6 rerun; a gate |
| Developer 13: items already exist | Minor | `detection/queries.py:105`, `usage/postgres.go:166-175`, `metrics/router.py:46,55` | The old T5.5's fallback removed; T8.4 picker work only; T8.3 adds the missing route; `Gaps` corrected |
| Developer 14: `service_findings` order | Minor | `detection/queries.py:105-127`, `detection/service.py:211-239` | `project_findings_in_window`; existing caller unchanged |
| Developer 15: `counted` unreachable | Minor | `middleware/ratelimit.py:148-156,299` | `scope["state"]`; AC16; T2.3 |
| Developer 16: edge cases | Minor | `selection.ts:21-27`; search of `apps/api/src` | `default_range` values, `dashboards/ranges.py`, AC141; 2,000 hard cap, restore at cap, AC78 |
| Developer 17: cancel setting | Minor | As Architect 10 | As Architect 10 |
| Developer 18: T9.1 and T10.1 | Minor | `justfile`, design's section 8, spec D6 | T8.1 writes its own fixture; T3.7 trial with its stop rule; T10.1 only if it passed |
| UX 1: on-call screen has no tasks | Blocker | Old plan | Plan rewritten; spec's words used; T6.8 a Playwright run at 375 px |
| UX 2: false calm from detection | Blocker | `FindingsScreen.tsx:149-152`, `headline.ts:73-98`, `detection/service.py:143` | FR63, design's *Detection's line*, words table, AC97, T2.14, T2.18 |
| UX 3: FR31 against FR48 | Blocker | Both requirements as they stood | One rule: FR31, FR48, D29; AC98 |
| UX 4: empty sentences claim good news | Major | Earlier words table; FR54 | Words table, AC99 |
| UX 5: partial failure invisible | Major | FR13, NFR5 | FR64, AC100, T2.18 |
| UX 6: page lines unordered | Major | `AppShell.tsx:55-69`, `TopBar.tsx:28-42` | Priority table; stale banner's words; AC101 |
| UX 7: picker on the wrong clock | Major | `WindowControl.tsx:81-82,100-103`, `RefreshControl.tsx:70` | FR46, AC64, T6.7 |
| UX 8: open finding freezes the page | Major | `refresh.ts`, `RefreshControl.tsx:52-56` | FR57, AC102 |
| UX 9: no one-step undo for a zoom | Major | Earlier design | History entry, page line, FR53, AC103 |
| UX 10: control's states undefined | Major | `RefreshControl.tsx:38-47`, `refresh.ts:141-146` | State table, FR30, FR55, AC104, T6.3 |
| UX 11: words ignore the role | Major | Earlier words | Viewer's words, FR65, AC105 |
| UX 12: value line does not fit | Major | `uPlot.esm.js:3259-3279` | React legend, FR32, FR35, AC106, T4.7 |
| UX 13: shading cannot name its finding | Major | `TrendChart.tsx:214,259,286` | FR47, readout, table column, AC107 |
| UX 14: wireframes and states missing | Major | `console-redesign/spec.md` AC1, AC12; `TopBar.tsx:39-42`, `Sidebar.tsx:74` | 375 px drawings, builder's phone drawing, six-state table, questions answered, AC108 |
| UX 15: datalist cannot show escapes | Major | Read from the HTML standard, not run; `SelectField.tsx:4-9` | D46, FR23, AC74, T3.3 |
| UX 16: narrow tables and small charts | Major | `DataTable.tsx:12-14`, `TrendChart.tsx:33,37,382` | FR5, FR49, AC67, `layoutRows` |
| UX 17: "nothing has reported" has no data | Major | Earlier context JSON | `ever_reported`, `last_seen_at`, FR48, AC109 |
| UX 18: reason not first | Minor | `principles.md:16-23`, `design-system.md:123-126` | FR47, strip order, `severity` in the context |
| UX 19: one idea, several names | Minor | `RefreshControl.tsx:55`, `query-client.ts:82`, `WindowControl.tsx:47-53` | Wording table, FR45, FR53 |
| UX 20: full screen has no way out | Minor | FR55 | FR55, AC76, T6.9 |
| UX 21: side sheet capped | Minor | NFR6 | `clamp`, preview's width, AC112 |
| UX 22: true chart called old | Minor | `refresh.ts:128-131` | FR31, BR5, D29, AC110 |
| UX 23: correlation ids and structure | Minor | `api-client.ts:159`, `OverviewScreen.tsx:227-228` | FR60, NFR5, AC111, page state |
| Sales 1: plan does not build what is sold | Blocker | Old plan | As Developer 1; T3.7, T11.4 |
| Sales 2: founder decision wrong | Blocker | As Developer 2 | As Developer 2; real T11.4, a signed checklist |
| Sales 3: accuracy claims | Major | `services/queries.py:6-10,47,75`, `000008:22,27`, `000035:31-32` | D44, Goal 3, BR3, buyer row, *How far back*, FR70, AC113 |
| Sales 4: nothing to demo for 3 days | Major | `FindingsScreen.tsx:205-233`, `services/schemas.py:147-155`, `tools/detection-seed` | FR3, AC3, T1.7, T11.4 |
| Sales 5: no answer on noisy neighbours | Major | `policy.py:99` | As Architect 11; buyer row; T5.7 runs two tenants |
| Sales 6: packaging unclear | Major | `selection.ts:48-52`, `policy.py:15,303-311` | FR17, buyer rows, D61 (was founder decision 3) |
| Sales 7: Grafana objection unanswered | Major | Research; spec's table | Buyer rows, research corrected, script questions, T2.0 |
| Sales 8: competitor facts untraceable | Major | `grep` of research; pages re-read | Research's *Added in review*; spec's lines rewritten; the section this cites now exists |
| Sales 9: wording overstates scope | Major | FR18, foundation `:613` | Sales line, FR18's words, *Must not be claimed* |
| Sales 10: histograms unsaid | Minor | `metrics/router.py:37`; spec's table | Buyer row, rivals ahead, D49, script |
| Marketing 1: fallback wrong | Major | As Developer 2 | As Developer 2 |
| Marketing 2: Netdata untested | Major | Its docs and README, read 2026-11-02 | Research, spec's sales line, candidate wording labelled unproven |
| Marketing 3: deploys overstated | Major | Datadog and Grafana pages | Research corrected; spec's rivals list; sales line reordered |
| Marketing 4: nothing visible on day one | Major | F6 spec `:214-220`, FR18 | FR63, T11.4, sales wording |
| Marketing 5: launch gate cites nothing | Major | Old plan | T11.1 full list, T11.4, T11.5, release note |
| Marketing 6: name contradicts vision | Major | `vision.md:16`, `feature-catalog.md:27,83,309` | Spec and plan titles, *Builder* defined as internal, positioning line for the founder |
| Marketing 7: facts unmarked | Major | `grep` | As Sales 8 |
| Marketing 8: SigNoz out of date | Minor | SigNoz's pages | Research table and *Not checked*; spec's rivals list |
| Marketing 9: last-viewed is not a difference | Minor | Datadog's API page | Research corrected; *Must not be claimed* |
| Marketing 10: "most rivals" unsupported | Minor | Research's table; pricing pages | Named facts; old line marked superseded |
| Marketing 11: security headline | Minor | `research.md:24-25`, `spec.md:135` | Corrected and marked |
| Marketing 12: script does not test the line | Minor | Spec's script | New question; status corrected |
| Customer 1: plan does not approve this product | Blocker | As Developer 1 | As Developer 1; T11.7's script |
| Customer 2: 7 days on what pays 395 | Major | FR17, `selection.ts:51-53` | FR17 lesser-of, T5.7 at 30 and 90 days, buyer row, D50 |
| Customer 3: histograms; "all" contradictory | Major | `metrics/router.py:37`, design as it stood | D48, D49, AC52, buyer row |
| Customer 4: panels die silently | Major | `000003_metrics_sum.up.sql:27`, `dbstats/queries.py:13-18` | `series_changed`, FR16, AC115, list's count, builder hint |
| Customer 5: limited hardest in an incident | Major | Arithmetic re-run: 40 + 40 per drag at 90 a minute | FR53, NFR12, rounding, visible panels, AC127 |
| Customer 6: no off switch | Major | Earlier design; `nexovue.yml:102` | NFR14, FR69, AC91, AC116, T5.6b, T5.6c |
| Customer 7: templates unwritten | Major | Spec's AC80; `conventions.py:81,88,91`; `OverviewScreen.tsx:186-193` | *The templates*, AC80, AC129 |
| Customer 8: right edge reads low | Major | `services/service.py:73-80`, `rollup.py:45-53`. Read, not run | FR66, AC114, T4.9, separate bug record for Overview |
| Customer 9: threshold alerts bypass this | Major | `messages.py:128`, `models.py:1344` | FR57, FR67, AC117, T7.8 |
| Customer 10: trial's landing page | Major | As Sales 4 | As Sales 4; founder decision 2 rewritten |
| Customer 11: no sized path off | Major | The ten boards in `tools/otel-demo/upstream` were opened: 143 panels, by script | T2.0, buyer rows |
| Customer 12: cannot read now | Major | `RefreshControl.tsx:94-101`, FR29 | FR71, AC118, T6.3 |
| Customer 13: no normal on charts | Major | `detection/baselines.py:117` | **Rejected whole,** above |
| Customer 14: false support sentence | Major | `superadmin/seed.py:11-13`, `auth/dependencies.py:130` | FR62, AC119, AC142, T2.19, T6.10 |
| Customer 15: two clocks | Minor | `WindowControl.tsx` | As UX 7 |
| Customer 16: picker dashboards hide the board | Minor | AC82 as it stood | FR57, AC82, list route |
| Customer 17: self-hosted tuning | Minor | Earlier design; `policy.py:14` | D36, T5.6c |
| Security 1: controls have no task | Major | Old plan; `lifespan.py` | As Developer 1, 6 and 7 |
| Security 2: no prompt removal | Major | `members/queries.py:98-105`, `members/service.py:225-232` | FR68, D47, route, AC120, T7.7, FR62 |
| Security 3: new route has no gate | Major | `metrics/router.py:48,73`, `metrics/queries.py:95-99`, `mask/rules.go:26-38` | Design's *That route's gate*, AC9, AC121, T8.3, FR42 |
| Security 4: planted versions as deploys | Major | F28 `design.md:496,526,543` | As Architect 3 |
| Security 5: tenant starves tenant | Major | As Architect 11 | As Architect 11 |
| Security 6: deception incomplete | Major | HTML standard; design as it stood | FR61, BR10, BR14, AC35, AC74, AC123, FR53 |
| Security 7: link validation stops at scheme | Minor | FR56; design | FR56, FR27, FR62, AC124 |
| Security 8: history erasable | Minor | D19 and design as they stood | FR51, D19, AC56, AC125, table |
| Security 9: memory bound | Minor | As Architect 4 | As Architect 4; per-organization share |
| QA 1: plan out of date | Blocker | As Developer 1 | As Developer 1 |
| QA 2: Step 1 breaks a link | Major | `messages.py:128,130,240,325,327,378` | T1.5, T1.6, FR43, AC54 |
| QA 3: NFR11, NFR12 have no criterion | Major | `metrics.py:84,113-122`, `nexovue.yml:41` | NFR11, NFR12, AC126, AC127, T5.7 |
| QA 4: settings change detection | Major | `detection/store.py:53`, `tenant_statements.py` | As Architect 10 |
| QA 5: findings read regresses F6 | Major | `detection/queries.py:105-123`, `test_detection_status_api_live.py` | As Developer 14; T2.14's live case |
| QA 6: browser checks outside the gate | Major | `justfile:279-281`, `ci.yml:77-78` | D51, named specs, T4.6 claims AC31's data half only, T4.10 widened |
| QA 7: no list of what cannot be proved | Major | Old plan | *What these tests cannot prove*; T11.7 a release condition |
| QA 8: requirements with no criterion | Major | Spec's ACs as they stood | AC3, AC55, AC71, AC111, AC119, AC124, AC130 to AC144 |
| QA 9: latency "to the digit" | Major | ClickHouse's docs, read; `services/queries.py:47`. **Not run** | Goal 3, BR3, AC14, AC39, D44, T2.8 |
| QA 10: shared reads tested simply | Major | Design as it stood | AC15 to AC17 from empty, AC59, AC86 to AC89 |
| QA 11: no seam for slow reads | Major | `justfile`, `reader.xml:29-32` | Test seam in the plan, AC63's tolerance |
| QA 12: AC9, AC53 unprovable | Major | Walker's chain as the reviewers reported, and `lifespan.py:21,137`, `test_detection_has_no_ai.py` read | T2.16, T2.17 |
| QA 13: import contradiction | Major | Spec's table and FR42 | D22, D53, FR42, route, AC128, T9.3 |
| QA 14: plan contradicts F28 | Major | As Architect 3 | As Architect 3; "must confirm Postgres" removed, since F28 says so |
| QA 15: meter may be stopped | Minor | `worker/run.go:291,307-311` | FR16's words, the old T5.5's fallback removed, AC21 |
| QA 16: AC43 compares code with itself | Minor | `git status`; design | T4.3, T4.4 |
| QA 17: validation plan names no alert suite | Minor | `jobs/expiry.py:32-42` | Regression runs in the validation plan; T7.6 at 29 and 31 days |
| QA 18: datalist | Minor | As UX 15 | As UX 15; AC74 widened |

### Found while checking, beyond what was reported

- **The first review's record was never written.** The spec and design cited `status.md`, *Reviews*, which did not exist. This section records the second round only. What round one changed can only be read from the spec's decisions.
- **`clickhouse_connect` error handling was not read,** so how a code such as 202 arrives is left to T5.6b, and said so.
- **`coverage_of`:** this note said F28 stated it two ways. F28's `design.md` now states one, `coverage_of(reported_at, seen_at, *, at)` (`:382,555`), which F20 cites.
- **The Overview screen has the partial-bucket fault too** (read, not run). It gets its own record at T4.9, and is not absorbed here.
- **The reviewers' `T5.7` depends on data the Mac may not hold.** The plan says the machine must be stated, and the test server's price stays the founder's open question in `phase-2-plan.md`.

### Follow-up review, 2026-11-02

Fourteen further findings. All fourteen were checked against the code or the documents named, and all led to a change. Thirteen were fixed as proposed. One was fixed by removing the claims, because the vendor pages could not be read. Nothing was built or run, so "fixed" means the documents now answer it.

| Finding | Severity | Verified against | Changed |
|---|---|---|---|
| AC84 cannot pass as written | Major | `middleware/ratelimit.py:67,96-102,199-209` | AC84 in two cases, design's *Time budget*, T2.12 |
| The fallback is not buildable | Major | Plan's T3.2, T3.6, T7.9 as they stood; design's route table; spec's templates | `DashboardActions`, TF.1, TF.2, *Judging on this path*, D55, D56, spec's decision 1, the Developer 2 row |
| Step 2's judging step cannot be run | Major | Plan's T2.18, T5.5, T5.6a; spec's *Start from Overview* | Judged on what exists; notes and findings moved to Step 5; AC61's row split |
| AC74 cannot run on phone Safari | Major | `tools/e2e/playwright.config.ts:62-68` | AC74 split, T3.3, T5.6a, T7.3, *What these tests cannot prove*, D57 |
| AC107 is stricter than FR50, and its proof cannot test it | Major | FR50, T4.10, `design.md`'s no-canvas line | AC107, T4.7, T4.10, its Traceability row |
| Moves into `shared/` break the import direction | Major | `overview-plots.ts:17`, `headline.ts:5`, `OverviewView.test.ts:17`, `frontend.md:3`, `service-detail.ts:9-10` | Types named in T1.7, T2.18, design, FR63. `findingSpans` checked: `FindingMarker` is already shared, so T5.5 moves no type |
| The spec contradicts itself on a whole-request 429 | Major | `query-client.ts` (`handleApiFailure`, `shouldRetry`), `api-client.ts:202` | Error table, design's two tables, new AC145, T2.18, T6.4. Also found: the design said the query retries, and `shouldRetry` never retries a 4xx |
| `TICKET_ROUTES` does not exist | Minor | `ai-chatbot/design.md:236,271`, `plan.md:137` | The name is `TICKET_HANDLERS`, and it matches handlers: AC53, design, T2.17 |
| AC14 names a route that does not exist | Minor | `synthetics/router.py:40,173` | AC14 |
| Stale citations into F28 and F19 | Minor | F28 `design.md:382,552,555,569,756`; F19 `design.md:468,479,508` | Section names in spec, design, plan. The older F28 note above corrected. Historical rows in the table above are left as they were |
| No `DataTable` test exists | Minor | `grep` of `apps/web/src`, `shared/components/` listing | T7.2, design's *The list* |
| T5.6b omits the dev stack and test account | Minor | `docker-compose.yaml:203`, `environment.py:21`, `test_clickhouse_reader_live.py:28-30`, `justfile:48-49` | T5.6b, with T5.7 on `just dev-app`. Added `test_clickhouse_reader_live.py`, which the finding did not name, and corrected `.env.example`'s path |
| Two competitor claims have no source | Minor | `research.md` Datadog row and *Alerts from a panel* | **Removed from `spec.md`, not proved.** On 2026-11-02 the search and page tools were out of quota, so no vendor page could be read. Datadog's dashboards as code and SigNoz's alerts from a panel may both be true. T11.5 must read them before either is said |
| Small drifts against the foundation | Minor | Foundation `:248,278-288,484`; design as it stood | `PURGE_DASHBOARDS` in the differences table and impact lines, section references, the stray fragment removed, `.purged` in *Audit* |

**Nobody has reviewed these changes either.**

### What remains as a risk after the reviews

- **Nobody re-reviewed these changes.** They added about 60 acceptance criteria and a new ClickHouse account, on a feature whose evidence is already weak. The scope grew again.
- **F20 has grown large for a feature no buyer has asked for.** Founder decision 1 matters more now than when it was written. Templates only remains the fallback.
- **All API screens now share a server-side bound.** A screen could fail at its limit where it would not have. Its values are guesses.
- **`/` shows charts for the first days, but no findings.** The demo gate needs T11.4's seeded project, and a real trial will not have that.
- **The differentiator is unproven.** It is worded as what nexovue does. Netdata, Datadog and Grafana all mark charts in some way, and two free editions were not checked.
- **A chart cannot show "normal" (D54),** which is what the product is about.
- **No histograms on dashboards (D49), no bulk export (D39), no scripted read (D9), and raw metric windows of 7 days at first (D50).** Each may lose a buyer.
- **Browser-only checks can regress between named runs (D51).**
- **Latency tolerance is unknown** until T2.8's check is run.
- **A hostile telemetry value** is escaped and never run, but only a browser pass proves it on a real canvas.
- **A purge cannot reach backups.**
- **The cancel setting** was read from ClickHouse's master source, not the pinned 26.3, and has not been run. The fixed 15 s limit is what holds if it fails.

## Risks

- **Step 3 residuals (2026-11-09):**
  - **Not run:** both new Playwright specs (`dashboards-picker.spec.ts`, `dashboards-keyboard.spec.ts`): no dev stack was reachable. The founder's judging step for Step 3. **The picker on phone Safari and Chrome (AC74, the plan's "try the control before relying on it"): not yet run, founder checks after Phase 2** (foundation items 27, 28), with device and date recorded here. A screen reader pass on the builder is T11.7.
  - **The worst case is counted twice**, by `compile.py` at save and by `draft.ts` for the count on screen. A test holds the caps equal, not the counting; Step 5's kinds must change both, and the API stays the judge (its refusal is shown in the summary).
  - **`large` still draws at the chart's one height** in the builder, as on the view, until T4.4.
  - **A new dashboard's previews use the browser's clock** (open decision 3).
  - **"Reload it" after a conflict drops the draft,** and says so on screen; "Save as a copy" keeps it under "<title> (copy)", which the API refuses if that title is taken, and the summary says so.
  - **The builder's context read refreshes on the dashboard's interval** (Postgres only), as the view's does; it is only used for the project's name and clock.

- **Step 2 residuals (2026-11-08):**
  - **The founder's judging step has not happened in a browser** (click *Start from Overview*, save it, see three charts matching Overview's, read the log line, stop ClickHouse and see the panels and the one line). No full stack was brought up on this shared machine.
  - **AC21 was run over the rows the meter and the rollup write, not through ingest** (`test_dashboard_panels_live.py`): the receiver, Kafka and the workers were not started. The rule is proved; that the meter writes `service_last_seen` for every service is still "read, not run" (spec, *Dependencies*).
  - **AC84's case of a Postgres pre-read waiting on the pool** is not tested; its two slot cases are, with real slots in the real limiter.
  - **Assumptions inside the design, not product behaviour,** chosen where the documents named no number: a panel title and a link label at most 120 characters (a dashboard title's bound), a link address at most 2,048, a panel id `^[A-Za-z0-9_-]{1,32}$` ("a short pattern"), a refused cap wait's `retry_after_seconds` the 15 s statement limit (a place frees by then at the latest), and the invisible characters as Unicode categories Cc, Cf, Zl, Zp, Cs and Co on both sides. A description-only change bumps the lock version and writes an audit entry but keeps no version row, since FR51 and the design keep versions for title or definition changes only.
  - **What Step 2 does not draw yet, by the plan:** `large` panels draw at the chart's one height until T4.4's `height`; the line under a note (FR62) is T6.10; the design's metrics (a scans histogram, a state counter) have no task in the plan and are not built; `panels_not_reading` is written by T7.1; catch-up reads after `refused` and the stale banner are T6.4.
  - **Two layout corrections to the plan's paths** (no behaviour change): flat feature folder, view test at the app layer (plan T2.18's note).
  - **`cancel_http_readonly_queries_on_client_close` works on 26.3.20.7** (AC63, below), but a handler is not cancelled when its client leaves (uvicorn does not cancel it), so the leader waits to its deadline and the statement then ends at its fixed limit: the cancel setting helps only when a task loses its last reader before its statement ends.
- **Step 1 residuals (2026-11-02):** the founder's browser judging of the new front door has not happened (no stack was brought up); `console-routes.json` is a second copy of the route list, held to the router by `route-manifest.test.ts` and to the scheduler by `test_alert_links.py`, and drifts only if both tests are weakened; the design's proposed ADRs were unfiled then (two are filed with Step 2, 2026-11-08).
- **No buyer has been asked** whether a builder matters, or which parts. The evidence is weak, and this is the largest piece of Stage 6.
- **No cap is published before deployment** (foundation item 9: no rented test server). T5.7 runs on the Mac, which shares 11.7 GiB with the demo, and is rerun on Oracle before any cap or "how many at once" is stated. Oracle's free allowance (2 Arm CPUs, 12 GB) is below the 16 GB minimum, so that rerun needs a paid shape, which returns to the founder at deployment.
- **Every cap is a guess** until the plan's T5.7: 30 panels, 40 scans, 4 at once, 5 and 2 per process, 3 and 1 per organization, a 60-second default, a 30-second floor, a 20-second deadline, a 15-second statement limit, 7-day raw windows, the shared read's rows, and the API account's limits.
- **No AI building, no public links, no query language.** Rivals have all three, and a buyer may notice.
- **Wall displays need a signed-in person,** and a session lasts seven days (`settings.py:213`).
- **Unproven:** 30 charts with a shared cursor; drag in the real builder (the trial on a throwaway copy passed on conditions, 2026-11-09, *Drag trial*); a tap on a phone; a native select with escaped labels on phone Safari; `service_last_seen` holding a row for every service, which is read and not run.
- **A suspected metric bug** in `HISTOGRAM_SQL` and `HEATMAP_SQL`, from reading only. The plan's T8.1 tests it. If it is real, it needs its own record.
- **Sales wording** must stay in the future tense, and competitor facts must be re-read before any public claim (T11.5).
- **Personal data in notes and attribute labels.** Masking does not reach what a person types, and it reaches only credential-shaped telemetry. Purge is the path.

## Validation

### Step 3, run 2026-11-09

On this machine, with my own stores: Postgres `f20s3-pg` (pgvector 0.8.6, Postgres 18, port 55432, migrated to 0056) and ClickHouse `f20s3-ch` (26.3.20.7, port 58123, the 39 migrations), on network `f20s3-net`; all three removed at the end. Counts read from the output.

- **Seen failing first, then passing:** T3.1's offline cases (`test_dashboard_panels.py` could not import `DraftRequest`), `test_authorization_coverage.py` and `test_dashboards_routing.py` (1 failure each, naming `preview_panels`); `last_viewed_at` (`test_dashboards_live.py`, `KeyError`); `draft.test.ts`, `PanelPicker.test.ts`, `DashboardActions.test.ts` and `DashboardEditScreen.test.ts` (each failed to import its module; `PanelPicker.tsx` and `SideSheet.tsx` were moved aside for that run); the view's live region (1 failure with the region taken out); the list's links into the builder (1 failure against the old dialog); a preview refused as invalid (1 failure with `isInvalid` answering false); the builder's layout at `lg` (1 failure: `lg:col-span-4` where the view draws 6). **Two defects caught this way and fixed:** the preview's cache key named the Apply count and panel but not the draft, so a second sheet editing the same panel was served the first sheet's answer (a new case in `PanelPicker.test.ts`, seen failing); and the builder drew `xl` spans at `lg`. One test was wrong and corrected: a draft read right after a saved one cost nothing, which BR6 intends, so the equal-charge case uses two processes and a separate case holds the sharing.
  - **The three live preview cases** (`test_dashboard_panels_live.py`'s preview-equals-saved, the viewer's 403 in `test_dashboards_live.py`, the real-token route in `test_dashboards_real_tokens_live.py`) were first run after the route existed, so they were proved afterwards, on the same stores re-created, against a copy of `dashboardpanels/router.py` kept in scratch: **with the route removed**, all failed (4 failures: the equality case `404 == 200`, the viewer `404 == 403`, both token kinds `404 == 403`); **with the gate weakened to `CurrentIdentity`**, the two 403 cases failed (`200 == 403` each; the token case cannot, since `CurrentIdentity` already refuses tokens, so the removal run proves it); **restored from the copy** (byte-equal, by `cmp`), all passed (4 passed).
  - **Mutation checks on the builder's tests,** each reverted: every toolbar button a tab stop (1 failure), `disabled` in place of `aria-disabled` at the ends (2 failures), the unsaved-changes guard never firing (1 failure).
- **API, F20 and the suites it rests on, one run, live included:** 24 files: **679 passed, 1 skipped** (the conventions suite's "no wildcard to shadow"). **API offline, whole (`--ignore-glob='*_live.py'`, `-n 6`):** 4,646 passed, 1 skipped, 18 errors, all in the two suites that need the full dev stack and fail loudly without it (`test_filters_reach_past_the_cap.py`, `test_slo_system.py`), as in Step 2.
- **Scheduler:** `test_alert_links.py`, `test_alert_messages.py`, `test_detection_has_no_ai.py`: **44 passed** (the route list gained two routes).
- **Console:** `vitest run`, whole suite: **152 files, 1,788 tests, all passed** (new: `draft.test.ts` 14, `PanelPicker.test.ts` 14, `DashboardActions.test.ts` 5, `DashboardEditScreen.test.ts` 25; the view's test 21). `pnpm -r --if-present run typecheck`: `tools/e2e`, `packages/browser-sdk`, `apps/web` all `Done`. `biome check` on `features/dashboards`, `app`, `shared` and the two specs: no errors (4 warnings, all in untouched e2e files). `node packages/ui/scripts/verify-tokens.js`: "no default or raw colours outside tokens.css"; no string literal starting `from-`, `to-` or `via-` in the new files.
- **Python gates, from the worktree root:** `uv run mypy .`: no issues in 849 source files. `uv run ruff check .`: all checks passed. `uv run ruff format --check .`: 1,214 files already formatted.
- **Not run:** `just check` (the coordinator runs it); both Playwright specs (no dev stack); the phone hand check (founder, after Phase 2); the founder's judging step: create a dashboard by keyboard alone, add a panel with a real service, move it, widen it, preview, save, reload, and as a viewer see no edit and a refused `/edit` link.

### Drag trial (T3.7), run 2026-11-09

On a throwaway copy in the session's scratch folder, not in the repository, and nothing from it ships. `@dnd-kit/core` 6.3.1, `@dnd-kit/sortable` 10.0.0 and `@dnd-kit/utilities` 3.2.2 (all MIT; peers `react >=16.8.0`, read from npm on 2026-11-09), React and React DOM 19.3.1 in `StrictMode`, Vite 8.3.5: a 12-column grid of 14 panels of widths 4, 4, 4, 3, 3, 6, 8, 4, 12, 6, 3, 3, 4 and 8, sorted with `rectSortingStrategy`, `closestCenter`, a pointer sensor with a 5 px activation distance, and `CSS.Translate` (no scaling). Driven by `playwright-core` 1.64.1 in the machine's Google Chrome, headless, at 1300 x 1000, with seeded random drags from one panel's centre to another's in 25 steps. About an hour, well inside the day.

| Measure | Library's sliding preview | Other panels held still until the drop |
|---|---|---|
| Final order equals the intended move, pointer | 80 of 80 | 80 of 80 |
| Drags where two other panels overlapped on screen mid-drag | 21 of 40 | 0 of 40 |
| Console errors or React warnings | none (one 404 for a favicon) | none |

- **Keyboard sensor:** Space, one arrow, Space moved by screen position, not one place: ArrowRight took the first panel to fifth, ArrowLeft took the sixth to second, and two of eight presses moved nothing. It cannot replace the toolbar, and the toolbar stays (WCAG 2.2, 2.5.7; FR22).
- **Seen but not fixed in the trial:** in 8 of 40 held-still drags the page scrolled sideways while a wide panel was dragged past the grid's edge.
- **Result:** pass on conditions, which the founder confirms (*Open decisions*, 2). Not tried: touch (drag is not offered on touch, design), and a real dashboard's charts inside the panels.

### Step 2, run 2026-11-08

On this machine, with my own stores: Postgres `f20-pg` (pgvector 0.8.6, Postgres 18, port 35432, migrated to 0056) and ClickHouse `f20-ch` (26.3.20.7, port 38123, the 39 migrations), on network `f20-net`. Counts read from the output.

- **Seen failing first, then passing,** for every behaviour change: T2.1 (`test_policy.py`: `AttributeError: Action has no attribute EDIT_DASHBOARDS`); T2.2 (`test_deletion_live.py` collection error, then `KeyError: 'dashboards'` from the deletion until `delete_dashboards` existed); T2.3 (`test_scan_charge.py`, no module `ratelimit.charge`; then the charge landed on the default tier until T2.4's path); T2.4 (2 new coverage cases failed; AC25's "a rule matching no router" stayed red until the panels router existed); T2.5, T2.9, T2.10, T2.11 (each suite failed to import its module); T2.6 (`test_every_mutating_route_is_gated_or_exempt` named the three writes until `POLICY_OF`); T2.12 (`test_dashboard_panels.py` failed with its service set aside); T2.13 (9 routing cases failed); T2.16, T2.17, T2.19 (with a token-readable handler and `mcp` and `httpx` imports planted: 4 failed; the real-token suite with the planted handler: 3 failed); the live suites without the routers (`test_dashboards_live.py`: 27 failed; panels live and seams: 6 failed); the console's `layout-rows`, `visible-escapes` (missing modules), the manifest test against the old router (1 failed), and the view test against the old `query-client.ts` (AC61 and AC145 failed: a toast appeared). One real defect was caught this way: the kept-line rule first keyed on the answer's value, so an equal failing answer never re-rendered and an old line outlived its interval; it now keys on the update time and expires on a timer.
- **API, F20 and the suites it rests on, one run, live included:** 23 files (`test_policy`, `test_scan_charge`, `test_ratelimit_coverage`, `test_ratelimit_callers`, `test_dashboard_definition`, `test_bounded_repository`, `test_dashboard_caps`, `test_shared_reads`, `test_dashboard_panels`, `test_dashboards_routing`, `test_authorization_coverage`, `test_read_surface_structure`, `test_dashboards_have_no_ai`, `test_dashboard_outbound_imports`, `test_api_conventions`, `test_migration_rules`, `test_services`, `test_auth_routes`, `test_deletion_live`, `test_dashboards_live`, `test_dashboard_panels_live`, `test_dashboard_panels_seams`, `test_dashboards_real_tokens_live`): **627 passed, 1 skipped** (the conventions suite's "no wildcard to shadow"). `test_tenant_chokepoint_structure.py`: 25 passed.
- **API offline suite, whole, before the final lint fixes:** 4,467 passed, 1 skipped, and one failure found and fixed (`test_api_conventions.py` read the delete's `response_class=Response` as a file route; it now answers 204 as the SLO routes do). Two suites that need the dev stack and are not named `_live` errored for want of it, as designed (`test_filters_reach_past_the_cap.py`, `test_slo_system.py`).
- **Scheduler:** `test_alert_links.py`, `test_alert_messages.py`, `test_detection_has_no_ai.py`: **44 passed** (the route list gained two routes).
- **T2.8's measurement:** 30,000 samples in ten stored parts, read 20 times through `/services/overview`: p50, p95 and p99 never moved (488, 942, 987 ms; spread 0.00000). **AC14's latency tolerance is therefore exact equality,** as measured on ClickHouse 26.3.20.7; ClickHouse still documents `quantile` as non-deterministic, so a difference one day means measuring again (`test_dashboard_panels_live.py`, `LATENCY_TOLERANCE`).
- **AC63, recorded:** a statement whose only reader was cancelled ended **0.6 s** later in `system.processes`, so `cancel_http_readonly_queries_on_client_close` works on 26.3.20.7. **AC18, recorded:** through a real uvicorn server, a client that dropped after 1.5 s left 3 statements running (one organization's 3 of 5 places on a hosted install); none started after it left, and all ended at their fixed limit 4.8 s later (6 s from the start); every place was free afterwards.
- **Console:** `vitest run`, whole suite: **144 files, 1,667 tests, all passed** (dashboard view and list 20, `layout-rows` 7, `visible-escapes` 10, route manifest and sidebar 14). `pnpm -r --if-present run typecheck`: `tools/e2e`, `packages/browser-sdk`, `apps/web` all `Done`. `biome check` on every changed web folder: no errors. `node packages/ui/scripts/verify-tokens.js`: "no default or raw colours outside tokens.css"; no string literal starting `from-`, `to-` or `via-` in the new files. `apps/web/src` imports nothing from `@/features` under `shared/`.
- **Python gates, from the worktree root:** `uv run mypy .`: no issues in 808 source files. `uv run ruff check .`: all checks passed. `uv run ruff format --check .`: 1,169 files already formatted.
- **Go:** no Go module touched, so no `go vet` or `go test`.
- **Not run:** `just check` (the coordinator runs it; this machine is shared); `just e2e` (Step 2 names no browser spec, and no stack was up); the founder's browser judging; AC21 through ingest. The stores `f20-pg` and `f20-ch` were stopped at the end.

### Step 1, run 2026-11-02

Counts read from the output, not exit codes:

- **Every behaviour change's test was seen failing first, then passing.** Before the implementation: 11 web test failures across 5 files (`route-manifest.test.ts` 3 — no `/overview`, Home still `Overview`/`/findings`, `console-routes.json` missing; `Sidebar.test.ts` 3 — Findings href `/findings`, Overview not at `/overview`, 404 said "Back to Overview"; `headline.test.ts` 1 — `service_count` 0 read "Here is what this project reported"; `FindingsView.test.ts` 3 — no charts, no `/overview` link, no "Nothing has reported in this window"; `FindingScreen.test.ts` 1 — back link `/findings`), and 3 Python failures (`test_alert_links.py` both tests; `test_alert_messages.py` project notice still `/findings`).
- **Web unit tests:** whole suite, `vitest run`: **138 files, 1,533 tests, all passed** (14 new).
- **Typecheck:** `pnpm -r --if-present run typecheck`: all workspaces `Done`.
- **Biome:** `biome check` over `apps/web/src`: no errors in changed files (one pre-existing warning in untouched `infrastructure/databases/connection-health.ts`).
- **Scheduler:** `uv run pytest test_alert_links.py test_alert_messages.py`: **31 passed** (2 new in `test_alert_links.py`). `uv run ruff check .`: clean. `uv run mypy apps/scheduler`: no issues in 156 files.
- **Not run for Step 1:** `just check` (the coordinator runs it; this machine is shared), the live suites and Playwright (`just e2e` needs the dev stack, which Step 1's tasks do not require and was not brought up). Step 1 names no e2e spec. **The founder's judging step — land on the feed, see the three charts, reach Overview in one click, try a project with no telemetry, follow an alert's detection link — has not been done in a browser.**

### Document-stage validation, run 2026-11-02

- One throwaway FastAPI 0.162 app, in memory, on 2026-11-02: `GET /api/v1/dashboards/panels:read` answered 200 with the panels router first, and 422 with the `/dashboards/{id}` router first. That route is now gone.
- Text search of uPlot's built file: no `innerHTML`.
- npm metadata for `@dnd-kit/core` 6.3.1, `@dnd-kit/sortable` 10.0.0, `@dnd-kit/react` 0.5.3.
- ClickHouse's `src/Core/Settings.cpp` and `ErrorCodes.cpp`, fetched from the master branch on 2026-11-02, for four setting names and four error codes.
- A count of panels in the ten boards under `tools/otel-demo/upstream/src/grafana/provisioning/dashboards/demo/`: 143.
- A script over `spec.md` and `plan.md`: every FR, NFR and AC in the spec appears in the plan's *Traceability*, with no number that the spec lacks. Rerun after the follow-up review, adding AC145.

**Not run at the document stage:** any repository test, query, browser, load test or demo. All figures in these documents marked *guess* or *arithmetic* are not measurements. (Step 1's own runs are above.)

## Specification impact

The specification is new. Where it differs from `research.md` or the foundation, its opening table says so. On approval, the documents named in `design.md`, *Specification impact*, must change. None has been changed by this work. F28's `design.md`, *Specification impact*, and D30 now agree with this feature, and D14 no longer disagrees with them.

## Documentation impact

Now disagreeing with the code or with each other, and left unchanged, since this run may only write this feature's documents:

- ~~The four false "a fifth read is refused" comments~~ corrected in Step 1 (T1.8, 2026-11-02).
- ~~`feature-catalog.md`'s front-door line~~ corrected in Step 1 (T1.9, 2026-11-02).
- `phase-2-foundation.md` section 8 names `/dashboards/panels:read`, and does not list the other routes. Section 9 takes F28's markers, which now agrees (T11.2).
- `vision.md` speaks against "an empty builder". The customer-facing name is now *Dashboards*, and `phase-2-plan.md` still says "dashboard builder", as a record of what was approved.

## Cross-feature reconciliation, 2026-11-02

All seven Phase 2 features and the foundation were read against each other. Changes that touched this feature, all still Proposed:

- `slo_budget` counts no scan: a Postgres read of F19's stored status (design, spec).
- T2.17's `lifespan.py` work is shared: done only if F5 or F19 has not (plan, D38).
- The front door must also move F22's `?case=` search and F4's findings link (design).
- `/dashboard-panels/*`, `dashboard_versions`, `PURGE_DASHBOARDS` and `markers_in_window` are now in the foundation.

## Last updated

2026-11-09 (Step 3 committed: T3.1 to T3.8; three open decisions, none blocking)

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** the builder rule, full plan only if at least half (3 of 5, 4 of 8) would not buy without building their own panels, counting answers backed by a dashboard they built; otherwise templates only (item 21). The three public sentences, including "we never price by person" (item 14).
- **Changed, A: no rented test server.** T5.7 and T11.6 run on the Mac as build gates; no cap is published until they are rerun on Oracle after deployment.
- **Changed, B: no paid model calls.** F20 calls no model, so nothing changes here.
- **Open decisions:** none for the founder.

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 3 proposed ADRs are filed as their steps are built:** ADR-031 and ADR-032 with Step 2 (2026-11-08); the third, the API's own ClickHouse account, with T5.6b.
