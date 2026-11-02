# Status: dashboards (F20)

## Lifecycle status

**In development.** The founder approved the specification, design and plan on 2026-11-02. F6 merged (PR #24), and **plan Step 1 — the front door moves to the Findings feed — is built and validated, 2026-11-02**, on the worktree branch `wt/f20`, uncommitted: the coordinator reviews the diff and commits. Steps 2 to 11 are not started.


## Current task

Step 1 done, awaiting the coordinator's review and commit. Nothing else in flight.


## Completed

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

The coordinator reviews Step 1's diff and commits. Then Step 2 waits on the buyer round (the foundation's founder decision 8) and founder decision 1. **Still owed from "implementation start": the design's 3 proposed ADRs in `docs/decisions/`, numbered then** — they concern Step 2+'s dashboard infrastructure and were not filed with Step 1, which was built alone under D58 by a session scoped to Step 1's tasks only.


## Open decisions

**None for the founder.** Decisions 1 and 4 were approved on 2026-11-02, as recommended (*Founder answers, 2026-11-02*, below); 3 is D61. The builder rule is applied to the buyer round's answers. The authors' other decisions (D1 to D61, `spec.md`) stand unless the founder overrules them.

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

- **Step 1 is built** (F6 merged, D58); it waits only on the coordinator's review and commit.
- **Step 2 onward** waits on the buyer round (the foundation's founder decision 8) and decision 1, and under the approved stage order, on the chain.
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

- **Step 1 residuals (2026-11-02):** the founder's browser judging of the new front door has not happened (no stack was brought up); `console-routes.json` is a second copy of the route list, held to the router by `route-manifest.test.ts` and to the scheduler by `test_alert_links.py`, and drifts only if both tests are weakened; the design's 3 proposed ADRs are still unfiled (see *Next action*).
- **No buyer has been asked** whether a builder matters, or which parts. The evidence is weak, and this is the largest piece of Stage 6.
- **No cap is published before deployment** (foundation item 9: no rented test server). T5.7 runs on the Mac, which shares 11.7 GiB with the demo, and is rerun on Oracle before any cap or "how many at once" is stated. Oracle's free allowance (2 Arm CPUs, 12 GB) is below the 16 GB minimum, so that rerun needs a paid shape, which returns to the founder at deployment.
- **Every cap is a guess** until the plan's T5.7: 30 panels, 40 scans, 4 at once, 5 and 2 per process, 3 and 1 per organization, a 60-second default, a 30-second floor, a 20-second deadline, a 15-second statement limit, 7-day raw windows, the shared read's rows, and the API account's limits.
- **No AI building, no public links, no query language.** Rivals have all three, and a buyer may notice.
- **Wall displays need a signed-in person,** and a session lasts seven days (`settings.py:213`).
- **Unproven:** 30 charts with a shared cursor; drag with panels of mixed widths under React 19; a tap on a phone; a native select with escaped labels on phone Safari; `service_last_seen` holding a row for every service, which is read and not run.
- **A suspected metric bug** in `HISTOGRAM_SQL` and `HEATMAP_SQL`, from reading only. The plan's T8.1 tests it. If it is real, it needs its own record.
- **Sales wording** must stay in the future tense, and competitor facts must be re-read before any public claim (T11.5).
- **Personal data in notes and attribute labels.** Masking does not reach what a person types, and it reaches only credential-shaped telemetry. Purge is the path.

## Validation

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

2026-11-02 (Step 1 built and validated)

## Founder approval, 2026-11-02

The founder approved all eight shared founder decisions in [`phase-2-foundation.md`](../../product/phase-2-foundation.md#founder-decisions), each as recommended. Decisions this feature raises in its own spec are still open. The documents stay Proposed until the founder approves the spec.

## Founder answers, 2026-11-02

The founder approved every Phase 2 founder question as recommended, with two changes (foundation, *Founder decisions*, 9 to 24).

- **Approved as recommended:** the builder rule, full plan only if at least half (3 of 5, 4 of 8) would not buy without building their own panels, counting answers backed by a dashboard they built; otherwise templates only (item 21). The three public sentences, including "we never price by person" (item 14).
- **Changed, A: no rented test server.** T5.7 and T11.6 run on the Mac as build gates; no cap is published until they are rerun on Oracle after deployment.
- **Changed, B: no paid model calls.** F20 calls no model, so nothing changes here.
- **Open decisions:** none for the founder.

## Spec, design and plan approved, 2026-11-02

The founder approved [`spec.md`](spec.md), [`design.md`](design.md) and [`plan.md`](plan.md) on 2026-11-02, with the foundation. **The design's 3 proposed ADRs are to be filed in `docs/decisions/` at implementation start**, numbered then; none is filed yet.
