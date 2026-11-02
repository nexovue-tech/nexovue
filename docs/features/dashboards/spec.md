# Feature: Dashboards (F20)

## Status

**Approved** by the founder, 2026-11-02. No code has been written yet.
Stage 6 of [`phase-2-plan.md`](../../product/phase-2-plan.md), built after the chain, except the front-door move,
which ships as soon as F6 merges (D58).

**How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present. Line
numbers are for that working tree and will drift. One throwaway check was run (*Dependencies and constraints*); no
test, query, browser or load test was. Every number marked *guess* is not a measurement. Inputs:
[`research.md`](research.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md), and the code they cite.

**Changed after two rounds of persona review, 2026-11-02.** What the second round changed, what was rejected and
what remains a risk is in [`status.md`](status.md), *Reviews*. Requirement numbers were kept, so new ones are appended. **Nobody has re-reviewed the result.**

**Where this differs from what it was given.** Each is explained under *Decisions taken*:

| Source | It said | This says | Decision |
|---|---|---|---|
| Research R14 | API tokens may read dashboard definitions | Tokens read no dashboards at all | D9 |
| Research R10 | No variables in the first version | One dashboard-wide service picker, and nothing more | D7 |
| Research R7 | Drag added only "if asked" | Buttons ship first. Drag is planned and not promised, until a one-day trial in Step 3 (T3.7) decides | D6 |
| Research trap 2 | A panel status called `too_old` | Replaced by "data starts at", read from the data, and naming the plan where one applies | D13 |
| Foundation section 8 | One route, `/dashboards/panels:read` | `GET /dashboard-panels/read`, `POST /dashboard-panels/preview`, a Postgres-only `/dashboards/{id}/context`, and a gated `POST /dashboards/validate` | D3, D24, D41, D53 |
| Foundation section 5 | `EDIT_DASHBOARDS` is the only dashboard action | Also `PURGE_DASHBOARDS`, owner and admin only, for removing a pasted secret at once | D47 |
| F28's design | F20 reads `GET /releases/markers` over HTTP | F20 calls `markers_in_window`, the function behind that route, with F28's cap, `truncated`, `basis` and `state`, and uses `coverage_of` | D14 |

## Words used here

| Term | Meaning |
|---|---|
| **Panel** | One chart, number, table or note on a dashboard |
| **Panel kind** | One of a fixed list of things a panel can show, such as "one service's latency". Each kind compiles to fixed SQL |
| **Builder** | The edit screen, where a person chooses, places and saves panels. An internal word: customers read "Dashboards", and nothing sold says "builder", because panels come from a fixed list and there is no query builder |
| **Composed dashboard** | One nexovue arranges by itself. Today's Overview is the only one |
| **Definition** | The saved panels and how they are laid out: `schema_version`, refresh, default range, picker, links and panels. It holds no data, title or description |
| **Dashboard document** | What a file and a save carry: `title`, `description` and `definition` together |
| **Panel context** | The Postgres half of a dashboard page: project name and clock, detection's state, markers, findings, alert incidents. It keeps answering when ClickHouse does not |
| **Shared read** | A scan's answer kept for 30 seconds (*guess*) inside one API process, so people watching one dashboard cost the store one read. Bounded by rows retained, not by entries (D42) |
| **Scan** | One read of ClickHouse, the telemetry database. Far costlier than a Postgres row read (`ratelimit/policy.py:8-11`) |
| **Batch read** | One request that returns every panel on a dashboard |
| **Quota** | How many reads one person may start per minute. Telemetry reads: 180 (`policy.py:78`) |
| **Sprawl** | Dashboards multiplying until nobody knows which to trust |
| **Cumulative counter** | A metric reporting a running total since a process started. The useful number is how much it rose |
| **Wall display** | A screen left showing a dashboard all day |

## Problem

A team can only see what nexovue has decided to compose. Overview and the service screen answer common questions well,
and they cannot answer "put checkout's latency, payment's errors and the two database statements we care about on one
page". Today there is no stored dashboard at all: no table, no builder, no layout, and no way to read a general metric
over time (`docs/research/phase-2-code-map-2026-11.md`, F20).

Who feels this:

- **A buyer comparing tools** sees dashboards in every rival and none here. The evidence is that a builder is expected,
  not a reason to buy ([research](research.md), short version 1). No buyer has been asked.
- **An on-call engineer** has no single page for "the things my team watches" during an incident.
- **A team lead** cannot put a chosen set of numbers on a wall screen.

**A second, separate problem.** The founder's recorded decision is that the Findings feed is what a person lands on
(`feature-catalog.md:325`). Findings now exist on this branch, and `/` still shows Overview (`app/router.tsx:243-247`).
New users meet the wrong front door.

**What a simple builder would get wrong** (research, section 2, confirmed in the code):

- 30 panels each making their own read spend a person's whole quota at a 10-second refresh (arithmetic).
- A batch read that counts as one request would walk around the limit, because the limiter counts HTTP requests
  (`middleware/ratelimit.py:182-214`).
- Summing a cumulative counter gives traffic 360 times too high (`metrics/queries.py:175-181`).
- A pasted link opens with the reader's own project, because project and time window live only in the browser
  (`shared/selection.ts:65,73`). A colleague can read staging numbers under a production title.

## Goal

1. A person lands on the Findings feed. Overview stays one click away, complete.
2. A team can build as many dashboards as it needs, from a fixed list of trustworthy panels, starting from something
   useful rather than a blank page.
3. The same statement over the same fixed window gives the same request and error counts on a dashboard as on the screen it came from. Latency percentiles are estimates everywhere in nexovue, and may vary slightly between two reads (D44). A live window may differ by the time between the two reads.
4. A panel never lies: a panel that could not be read never looks like a healthy flat line.
5. Dashboards stay few and recoverable: anyone can see who owns one and when it was last opened, and a bad save or delete can be undone.
6. A dashboard costs ClickHouse what it really costs, and the limits that protect ClickHouse keep meaning what they say.
7. All of it works with AI off, and no AI touches it.

The aim is parity on a narrow set. It is not to match or beat Grafana (foundation section 12).

## Target users

From [`vision.md`](../../product/vision.md) and research section 3. **None of this comes from a customer: nobody has
been asked.** It is reasoning from the code and from public writing.

| Person | Wants | Surfaces |
|---|---|---|
| **On-call engineer**, 3 a.m. | "Is it my service? Did a deploy do it? What was the number at 02:14?" | Dashboard view, shared cursor, links out to the service |
| **Developer** | The one right dashboard; to start from a service rather than from nothing | List, templates, builder |
| **Team lead** | A wall screen that says plainly when it has stopped being current | Full-screen dashboard view, refresh, "last updated" |
| **Buyer** (CTO of a small team) | To tick "has dashboards" and not inherit a chore | Demo through templates |
| **Admin** | To control who edits, and to clean up | List sorted by last opened; audit log |
| **Viewer** | To read and change the window, unable to break anything | Dashboard view only |

**Not served:**

- **A power user who wants to write queries.** There is no query language. Grafana stays ahead, and that is accepted.
- **A person outside the organization.** No public links, no embeds.
- **A machine.** API tokens get nothing here (D9). A script keeps the existing read API. **There are no dashboards as code in this version:** a file can be saved and loaded by a person, one dashboard at a time.
- **A customer needing per-dashboard access lists.** Roles stay organization-wide.

## Scope

1. **The front door.** `/` shows the Findings feed. Overview moves to `/overview`.
2. **Stored dashboards.** Per organization, displayed against a named project. Create, read, change, duplicate, delete.
3. **A fixed list of panel kinds**, 18 at first, then two more: metric series and SLO budget (*Functional requirements*, FR6).
4. **The builder.** Add, edit, move, resize, remove panels, by buttons that work with a keyboard. Later, drag to reorder.
5. **Starting points.** "Start from Overview", a *Service health* template, an *Infrastructure* template, "start from this service".
6. **One dashboard-wide service picker.**
7. **A batch read** that merges equal scans, charges each real scan, and reports each panel's own state.
8. **Chart upgrades** every dashboard needs: panel height, a colour per line, a y axis that may leave zero, the value
   under the cursor, a shared cursor across panels, and a table view of any chart.
9. **Findings, the deploys you send us and versions seen in traffic on time panels**, matched to the service where the chart has one, with a plain list of them beside the charts. Numbers and tables carry no marks.
10. **Honest panels:** per-panel state, "data starts at", "last updated", and the project named in the header and address.
11. **Anti-sprawl:** owner, last opened, sort by staleness, a description, unique titles, title search.
12. **A dashboard's own refresh setting and default time range.**
13. **JSON export and import**, in nexovue's own format.
14. **A permission and an audit trail.**
15. **Undo:** the last 20 versions of a dashboard (*guess*), and a deleted one restorable for 30 days (*guess*).
16. **A full-screen view** for a wall display, hiding the sidebar and top bar.
17. **Reaching a dashboard from where you are:** "Dashboards showing this service" on a finding and a service, and a dashboard's own list of links such as a runbook.

## Out of scope

| Left out | Why | What would bring it back |
|---|---|---|
| A query language, query builder or free-form SQL | SQL built at runtime fails the tenant-safety test (`test_tenant_chokepoint_structure.py`). Size of the team. Foundation sections 9, 12 | A new decision. Never as free-form SQL on the hosted service |
| Grafana dashboard import | Its panels hold queries nexovue cannot run. A half-working import is worse than none. The setup docs map common panels to panel kinds instead, naming no rival (foundation section 12) | Nothing planned |
| Public links, embeds, scheduled reports | Public links add an unauthenticated way to reach the query path. Grafana limits them, and published CVE-2023-2801 against them. We did not compare products' security records. Catalog already excludes embedding (`feature-catalog.md:457`) | A named customer's request, then expiry and rate limits by design |
| General template variables | One service picker covers the sprawl problem (D7). Anything more is a query language by another route | Repeated requests for a second variable |
| Group by any label | Needs SQL built at runtime or a read measured 25 times slower (`metrics/queries.py:97-99`: 2.29 s against 0.09 s) | A measured need |
| Per-dashboard permissions, folders, tags | Roles are organization-wide (`auth/dependencies.py:163-185`). Folders treat sprawl by organizing it | Real teams too large for one list |
| Dashboards as code, Terraform, bulk export | Tokens read nothing here (D9), and history and restore answer lost work without a token. **A real gap against rivals** | A buyer who keeps dashboards in git and would not buy without it |
| Error groups over time as a panel | Research listed it. Only the ranked table ships, because the table is what the existing read gives and no route reads a group's trend | Repeated requests |
| Plugins, custom chart code, HTML or Markdown panels | Script running in a console that holds a session | Never for HTML |
| AI building or explaining dashboards | Foundation sections 9, 10. A write driven by attacker-controlled text. A real gap against Datadog, SigNoz, Sentry and Grafana | A new decision, with its own injection suite |
| Alerts from a panel | Would be a second alert system beside F21 | A link to create an alert may do later |
| Rewriting Overview as a stored dashboard | It works and is tested. Rewriting gains a customer nothing | Nothing planned |
| Two people editing at once | The second to save is told and loses nothing (FR19) | Evidence that it hurts |
| A kiosk login for wall displays | A wall display needs a signed-in person, and a session lasts seven days (`settings.py:213`). **A real limit:** the screen signs out weekly. Docs recommend a dedicated viewer account | A customer with a wall display says it hurts |

## User requirements

- **UR1.** I land on the Findings feed when I open the console, and can reach Overview in one click.
- **UR2.** A new user with no findings sees what the project is doing, and not an empty page.
- **UR3.** I can start a dashboard from Overview, from a template, or from a service I am looking at, without facing a blank page.
- **UR4.** I can add a panel by choosing what it shows, from choices that name my real services and checks.
- **UR5.** I can rearrange and resize panels using only a keyboard.
- **UR6.** I can see that my change works before I save it.
- **UR7.** I can paste a dashboard link into an incident channel, and my colleague sees the same project and, if I choose, the same moment.
- **UR8.** I can see which project a dashboard is showing, without having to trust its title.
- **UR9.** I can see deploys and findings on any chart, and read the exact number at any moment.
- **UR10.** I can drag across one chart and have every panel follow.
- **UR11.** I can tell at a glance when a panel could not be read, and when the whole screen has stopped updating.
- **UR12.** I can look at one dashboard for any service by picking the service, without a copy for each.
- **UR13.** As an admin, I can find dashboards nobody looks at, see who owns them, and remove them.
- **UR14.** As a viewer, I can read any dashboard and change its window, and I am never offered an edit that would fail.
- **UR15.** I can save a dashboard's definition to a file, and load it back, including into another organization.
- **UR16.** I can read any chart's numbers without seeing the canvas.
- **UR17.** I can undo a bad save, and bring back a dashboard deleted by mistake.
- **UR18.** A link someone sent me does not change what my console shows once I leave it.
- **UR19.** I can tell when a page's reads succeed but its telemetry has stopped arriving.
- **UR20.** From a finding or a service, I can reach the dashboards that show it.
- **UR21.** On a phone at 3 a.m., I can read the page and get an exact number.

## Business rules

- **BR1.** A dashboard belongs to one organization, and to no project. It is displayed against one project at a time.
- **BR2.** A panel is a kind from the fixed list, with checked settings. A definition can never hold anything resembling a query.
- **BR3.** Every number comes from the same statement and the same row-to-model step as the screen it came from. Over a fixed window, request and error counts must match exactly, and a latency percentile within the tolerance AC14 states, because ClickHouse's `quantile` is non-deterministic (its docs, read 2026-11-02). A live window may differ by the time between the two reads, and a shared read by up to its 30 seconds (*guess*).
- **BR4.** Reading a panel can never show another organization's data, whatever a definition or a file says.
- **BR5.** A panel that could not be read must never draw as a zero or as nothing, and must not go on showing an old line as if it were current. "We do not know" is stated in words (`docs/design/principles.md` rule 2). Two allowances, both FR31's: on a live window, the last line stays under a filled warning strip for one refresh interval, then goes; on a fixed window, it stays with no limit, because numbers for a closed period are not old, and the strip says that.
- **BR6.** Every scan the store really runs counts against the person's and the organization's limits. A batch never makes a scan free. An answer served from a shared read cost the store nothing, so it costs the reader nothing.
- **BR7.** Editing needs `EDIT_DASHBOARDS`: owner, admin and member (foundation section 5). A viewer reads. Anyone with the permission may edit, delete or restore any dashboard, and a version remains to go back to. Removing text at once (FR68) needs `PURGE_DASHBOARDS`: owner and admin only.
- **BR8.** No limit here counts people or is sold as an upsell. Limits protect the system, and the screen says so (foundation section 10; `vision.md:152`).
- **BR9.** Everything here is free on every plan. No feature is held back by plan (foundation section 10).
- **BR10.** Text in a dashboard (titles, descriptions, notes) is plain text everywhere. Never Markdown, HTML, images or rendered links. The only links are the separate validated `links` field (FR56), which the console draws itself. Titles, labels and panel titles may hold no control or invisible character and no NUL, which the validator refuses (FR61).
- **BR11.** No AI reads, writes or explains a dashboard in this feature. A later AI link must treat dashboard text as free text (foundation section 3).
- **BR12.** No two dashboards in one organization share a title, compared without regard to case.
- **BR13.** The displayed project comes from the data and the address, never from a title.
- **BR14.** Every telemetry value on a dashboard, and every release version, is attacker-controlled text (`alerts/payloads.py:204`; F28 `design.md`, *Data flow and interfaces*), and is only ever shown as text, with invisible and direction-changing characters made visible (FR61).
- **BR15.** Refresh never runs faster than the data can change: the floor is 30 seconds (*guess, set by FR33's measurement*).

## Functional requirements

### The front door

- **FR1.** `/` serves the Findings feed, and `/overview` serves Overview. The old `/findings` list address is removed, and `/findings/$findingId` stays, because alert messages link to it. The scheduler's project-level detection notice links to `/findings` today (`alerts/messages.py:378`), and must link to `/` in the same step.
- **FR2.** The sidebar's Home section lists Findings at `/`, Overview at `/overview`, Alerts. `route-manifest.test.ts` holds this to the router.
- **FR3.** With telemetry and no findings, `/` shows the existing calm summary, then Overview's three project charts, drawn from the same `/services/overview` answer the summary already reads (so no extra scan), a prominent link to Overview, and later the templates. A project still learning says so in F6's words, with when judging starts. When `service_count` is 0 (`services/service.py:141-151`), it says "Nothing has reported in this window", hides the zero readings and the charts, says how to start and links to the setup page, and suggests widening the window, because a quiet hour is not a new project. Never a blank page, and never a screen of zeroes. The feed stays the front door.
- **FR4.** This step depends only on F6, and contains no dashboard code. It changes one scheduler line (FR1).
- **FR43.** Every link the scheduler composes (`alerts/messages.py:128,130,240,325,327,378`) must resolve to a route the console serves. A checked-in list of the console's routes, held to `router.tsx` by `route-manifest.test.ts`, is what the scheduler's test reads, since the scheduler cannot import the console.
- **FR44.** Findings is the active sidebar item on `/` and on `/findings/$findingId`. The 404 screen's "Back to Overview" link (`router.tsx:758`) says where it now goes: "Back to Findings".

### Definitions

- **FR5.** Two named shapes. A **definition** holds: `schema_version`, `refresh_seconds` (30, 60, 300 or null for off), `default_range` (one of the allowed values below, or null to follow the reader's own), `service_picker` (true or false), `links` (FR56) and an ordered list of panels. Each panel holds: a stable `id`, `kind`, optional `title`, `width` (3, 4, 6, 8 or 12 of 12 columns), `height` (`small` 96 px, `medium` 132 px, `large` 240 px; *guesses*), and `settings` checked per kind. `small` is for number and note panels only: a chart that small has about 48 px of plot, and a time panel saved `small` is refused. A **dashboard document**, used by a file and a save, holds `title` (at most 120 characters), `description` (at most 500) and `definition`. Height applies to charts and notes. A table's height follows its rows.
- **FR6.** The panel kinds:

| Kind | Shows | Settings | Scans |
|---|---|---|---|
| `project_trend` | Project rate, errors or latency over time | signal | 1, shared with other project trends |
| `service_trend` | One service's rate, errors or latency | service or picker; signal | 1, shared per service |
| `top_services_trend` | Up to 5 lines, worst or slowest services | signal; order; count | 1 |
| `endpoint_trend` | One endpoint's rate, errors or latency | service or picker; endpoint; signal | 1. Rollup |
| `node_trend` | One machine metric over time | node; metric | 1, per node and metric. Raw data |
| `top_nodes_trend` | Up to 5 lines, the nodes highest on a metric, chosen at read time | metric; count | 1. Raw data |
| `check_trend` | One check's uptime or duration | check by name; signal | 1, per check. Raw data |
| `statement_trend` | One database statement's cost over time | statement hash and server; label | 1. Raw data |
| `project_number` | One project number, optionally against the previous window | measure; compare | 1, or 2 when comparing |
| `service_number` | One service number, optionally compared | service or picker; measure; compare | 1, or 2 |
| `open_findings` | How many findings are open | optional service or picker | 0: Postgres |
| `services_table` | Ranked services | order; rows up to 20 | 1 |
| `endpoints_table` | Ranked endpoints | optional service or picker; order; rows up to 20 | 1 |
| `dependencies_table` | Busiest dependencies | failing only; rows up to 20 | 1 |
| `error_groups_table` | Most frequent errors | optional service or picker; rows up to 20 | 1. Raw data |
| `database_statements_table` | Slowest statements, by hash | rows up to 20 | 1. Raw data |
| `web_vitals_table` | Core Web Vitals | optional device class | 1 |
| `note` | Plain text, at most 2,000 characters, with a line saying everyone here can read it | text | 0 |
| `metric_series` (later) | One gauge or sum over time: one chosen series, or all series combined into one line | metric; shape; service or all; one chosen series or all; aggregation | 1, or 2 when a chosen series comes back empty (FR16). Raw data |
| `slo_budget` (from F19) | An SLO's remaining budget | SLO by name | 0: a Postgres read only |

**The allowed values.** The validator and its tests read this table.

| Setting | Allowed |
|---|---|
| `signal` | Trends: `rate`, `errors`, `latency`. `check_trend`: `uptime`, `duration` |
| `measure` | `requests_per_minute`, `error_rate`, `p50_ms`, `p95_ms`, `p99_ms`, `service_count` (project only) |
| `order` | `worst`, `slowest` (`services/router.py:48`) |
| `count` | 1 to 5 |
| `rows` | 1 to 20 |
| `compare`, `failing_only` | true or false |
| `metric` (node kinds) | The names in `infrastructure/conventions.py`, `INFRASTRUCTURE_METRIC_NAMES` |
| `shape` | `gauge`, `sum` |
| `default_range` | `15m`, `1h`, `6h`, `24h`, `7d`, `30d`, or null. One Python constant, held by a test to `shared/selection.ts`, which adds `30d` through the screen's declaration (FR52) |
| `aggregation` | `mean`, `min`, `max`, `sum` |
| `service`, `node`, `check`, `endpoint`, `SLO`, statement hash | A name of at most 255 characters, or the picker where the kind allows it. Values are bound in SQL, never spliced |

- **FR7.** A definition must be refused on save and preview if it has an unknown field, unknown kind, a setting outside its kind's list, more than 30 panels (*guess*), more than 64 KiB in total (*guess*), or a worst case of more than 40 scans (*guess*). The worst case compiles the definition and treats every picker reference as a distinct service. The refusal names the count and the panels to remove. The same check applies to a file, a draft and a template, with no second path around it.
- **FR8.** A panel kind nexovue no longer knows, met when reading a stored definition, shows "This panel type is no longer available", and the page still draws.
- **FR9.** Live dashboards per organization are capped at 1,000 (*guess*), for protection only, and the refusal says so. Deleted ones count towards a separate hard cap of 2,000 (*guess*), so repeated create and delete cannot grow rows for 30 days. A restore at the live cap is refused, with words saying so.

### Reading

- **FR10.** A saved dashboard's panels are read by a GET naming only the dashboard, project, window and picked service, and optionally which panels. A draft is read by a POST carrying the draft, gated by `EDIT_DASHBOARDS`. A live window is named by its range id, from the same constant as `default_range`, so the server rounds it once and people on one dashboard ask for the same window.
- **FR11.** Panels asking for the same scan share one. Three panels of one service's rate, errors and latency are one scan. People watching one dashboard share scans for 30 seconds (*guess*) within one API process.
- **FR12.** Each scan past the first spends one quota place in the person's and the organization's buckets. The request's own place pays for the first. Scans at once are held by the batch's permits and the process's caps (FR13), not by further in-flight slots (D43). The request keeps the middleware's one slot.
- **FR13.** Scans run in layout order, so a refused or unfinished scan affects the panels lowest on the page. One request runs at most 4 scans at once (*guess*). Across all requests, one API process runs at most 5 dashboard scans at once of its 8 connections (*guess*; `settings.py:209`), and at most 2 of them on raw data (*guess*), so screens and the workers' inserts keep room. On a hosted or signup-enabled install, one organization may hold at most 3 of the 5 and 1 of the 2 (*guesses*), so another organization's panels are not refused by its wall displays. On a single-organization install, that share is the whole cap. Above the API, ClickHouse itself bounds what the API's account may use (NFR14).
- **FR14.** A batch returns 200 whenever the dashboard and project are readable and a ClickHouse client exists, with one state per panel: `ok`, `empty`, `needs_service`, `not_in_project`, `ambiguous`, `series_changed`, `too_many_series`, `window_too_wide`, `over_limit`, `refused`, `timed_out`, `failed`, `paused_by_operator`, `unknown_kind`. With no client yet, it answers 503 (`deps.py:83-93`), and the page treats every scan panel as failed (FR48), with no retry and no toast.
- **FR15.** A panel's result also carries: when it was read; when its data starts, if later than the window's start; when its newest data is; how many series were combined, if any; which bucket is still filling, if any (FR66); and, when `refused`, how long to wait.
- **FR16.** `not_in_project` means this project holds no record of this service or check, and says only that ("No record of checkout in staging"), because a stopped usage meter (`apps/workers/internal/worker/run.go:291,307-311`) or a first flush not yet run would make a stronger sentence false. `empty` means a record exists and nothing arrived in this window, and it says when the service was last seen. A service in another project must never read as a quiet one. A node has no such record, so an absent node is `empty`: "node-3 has not reported in this window". A project-wide panel with nothing is `empty`, "Nothing reported in this window". A check name matching two checks in the project is `ambiguous` and draws nothing, because check names are not unique (`models.py`, `Check`). **A chosen metric series that is empty while the metric still has rows is `series_changed`:** "orders_processed still reports, under different attributes. Choose again", because adding one attribute changes the hash (`000003_metrics_sum.up.sql:27`), and "has not reported" would be false.
- **FR17.** Panels reading raw telemetry rather than per-minute rollups (the "Raw data" rows in FR6) refuse a window over their reader's limit, with `window_too_wide`, a sentence saying the limit and a button that sets the widest allowed window. Each limit is the lesser of a per-reader safety limit set by T5.7 and what the plan keeps. Starting safety limits: 30 days for `check_trend` and for `node_trend` on one node, 7 days for the others (*guesses*). T5.7 measures `node_trend`, `top_nodes_trend` and a single-service `metric_series` at 30 and 90 days, and raises a limit where the reads hold. A customer paying for 395 days of metrics may still be limited here, and the buyer table says so. The rest of the page still draws.
- **FR18.** The panel context carries the project's name and time zone, the window, detection's state (FR63), markers in the window (each cap and its `truncated` flag stated on screen, in F28's words: "showing the newest 500 of this window"), findings open during the window with their severity, alert incidents (FR67), open-finding counts, each service's coverage from `coverage_of`, and whether the project has ever reported, with its last-seen time. Service panels draw their service's markers and findings, plus project-wide findings. `top_services_trend` draws those of its listed services. Project panels draw all of them. Node, check, statement and metric panels draw all the project's, and say "All findings and releases in this project", so nobody reads a checkout finding on a disk chart as a claim about that disk. A **reported** marker is signed by the customer's CI, and reads as a deploy. A **seen** marker comes from telemetry, which a stolen ingest key can plant (F28 `design.md`, *Data flow and interfaces*), so it is drawn with a different rule, reads "Version seen in telemetry", and is never called a deploy. A service with coverage `none` says F28's words, "We receive no deploy information for this service", with a link to setup, so an empty chart never reads as "nothing shipped".

### Editing

- **FR19.** Saving sends the version it was based on. If someone else saved first, the save is refused, and the editor offers to reload or save as a copy. No work is lost.
- **FR20.** An unsaved change warns before leaving, through the existing guard (`UnsavedChanges.tsx`), meaning a value differs from what is stored.
- **FR21.** Every panel can be moved earlier and later, made wider and narrower, made taller and shorter, duplicated, edited and removed, using one toolbar per panel with one tab stop and arrow keys. Each action names its panel for a screen reader, and focus stays put at the ends and after a change. Every change is announced. Remove can be undone within the draft ("Removed Rate. Undo"). Below `lg`, the moves read Up and Down, and width is hidden.
- **FR22.** Dragging a panel to reorder it is planned, over the same stored order, and is decided by a one-day trial in Step 3 (T3.7), recorded in `status.md`. If the trial passes, it ships in Step 10. It must not replace the buttons (WCAG 2.2 criterion 2.5.7).
- **FR23.** Choosing a panel's settings offers real values (services, nodes, checks, statements and, later, metrics) read from the displayed project, in a side sheet: a native select showing each value through FR61's escapes while holding the raw value, plus a separate "Type a name" field for a service that has not reported yet. Choosing a value that holds escaped characters warns. A loaded list that failed says so, and the typed field still works.
- **FR24.** Applying a panel's settings previews that one panel, with the live window, inside the sheet.
- **FR25.** Deleting says what will go, in real numbers ("*Checkout on-call*, 12 panels, last opened 2 hours ago"), and that it can be restored for 30 days. Restoring a title now in use asks for a new one.
- **FR26.** Duplicating asks for a new title and shows the existing dashboards' titles, because copying is the sprawl Grafana itself warns about.
- **FR27.** Every create, change, delete, restore and purge writes an audit entry: `dashboard.created`, `dashboard.updated`, `dashboard.deleted`, `dashboard.restored`, `dashboard.purged`, with title, previous title on a rename, panel count, version, a SHA-256 of the definition, and the hosts of links added or removed, never the definition itself. A purge entry holds no removed text. Export and viewing write none.

### Viewing

- **FR28.** The address carries project, window and picked service, as ids and names (a copied link shows the service's name, and the copy menu says so). On opening, the address decides what this page shows and the top bar shows the same, so the header and the data cannot disagree. Changing the top bar on purpose changes the address and is remembered, as it is on any screen. See FR53.
- **FR29.** A viewer may change window, picked service, pause and this tab's refresh interval (FR71), none of which is saved. "Copy link" offers "Link to these exact times (02:00 to 03:00)" and "Link that stays live (last hour)", exact times first when the window was dragged or fixed, and confirms which was copied.
- **FR30.** Refresh follows the dashboard's setting: default 1 minute, or 30 s, 5 minutes, off, unless this tab chose its own (FR71). The console-wide 5 and 10 second options do not apply (`refresh.ts:34-40`). A fixed window never refreshes, as today (`refresh.ts:123-133`). A hidden browser tab does not refresh, and returning to it re-reads only if the data is past its interval. The design's state table says, for live, paused, fixed window, refresh off and returned from hidden, what the control shows, whether Pause exists, what the page line says, and whether the banner may fire. The banner must not fire when paused, fixed or off.
- **FR31.** From the first failed read, the page shows "Last good reading 02:14", visible at phone width. On a live window, a failed panel keeps its last line for one refresh interval, at most 5 minutes (*guess*), under a filled warning strip: "Could not read since 02:14. Line below is old." After that the line goes, and the panel shows only that sentence, shrinking once and not per refresh. **This holds in a page-wide failure too:** panels with an earlier reading keep their line for that interval, and the page line says the same thing once (FR48). A panel regrows only after a successful read. On a fixed window, the line stays with no limit, and the strip reads "Could not read again. This chart is from 02:40 and covers a closed period." A change of service, project or window never carries an old reading across. When no scan panel has been read for three refresh intervals, at most 5 minutes (*guess*), the page shows a banner a person can read from across a room: "NOT CURRENT. Last good reading 02:14". It is the later, louder form of the outage line, not a second message. The time the top bar shows is this dashboard's own last good reading, never the newest answer of any query (`RefreshControl.tsx:157-169`). With refresh off, it reads "Refresh off · showing 02:14" at all times.
- **FR32.** Cursor position and value show on every time panel, and moving the cursor on one shows the same moment on all. A single-series chart has a one-line readout of fixed height. A multi-line chart has one row per line, up to 5, with the height reserved so the panel does not move, each with its name and value, ordered by current value. uPlot's own legend stays off here, because its labels are mouse handlers that hide or isolate lines (`uPlot.esm.js:3259-3279`). The readout also names what is at the cursor: "02:14 · 412/min · Finding: checkout is slower · Deploy v2.1". Dragging a range on one moves the window of all (`TrendChart.tsx:110`), once.
- **FR33.** A measurement against the demo must set the panel cap, scan cap, refresh floor, raw windows and concurrency caps before release, against pass marks committed before it runs, and it must be run again after the last read is built (T11.6). Until then, those values are guesses (`design.md`, *Performance and scale*). It is a gate, not a report. Its result is from the demo, and must say that it does not prove customer volume.
- **FR34.** Every chart offers its numbers as a table, for accessibility and for reading exact values.
- **FR35.** Each panel's header names what it shows, with a generated default title that always includes the service ("checkout · Latency"), as a real link that opens that service's screen with the project and window kept. On `top_services_trend`, the names in FR32's rows are real links, reachable by keyboard, and activating one hides no line. Lines on a canvas are not clickable.
- **FR45.** A refused panel reads again by itself once, at the wait the server gave and up to 3 times (*guess*), reading only the refused panels, including on a fixed window or with refresh off, because that read never finished. While paused or hidden it waits, and says "Too many reads just now" with "Read again". The words say "Reading again in 20 s" only while that is scheduled. After the third try, it says "Too many reads just now" with "Read again" and the correlation id. The same sentence is used for a whole-request 429 on this screen, in place of the console's toast.
- **FR46.** Every time on a dashboard (axis, readout, table, "Data starts at", banners, the top bar's window phrase, the paused chip and the range picker) is on the project's clock while a dashboard is mounted, and the header names it: "production · last 1 hour · times in IST". The range picker reads and writes in that zone, and labels its fields "Times in IST", so an engineer in another zone who reads 02:14 on a chart can enter 02:00 and get that hour.
- **FR47.** Under the header, a plain "In this window" strip lists open findings first, each as the feed's own sentence with its severity badge and a link to `/findings/$findingId` with the window kept. Closed findings follow, marked "ended 02:40", then alert incidents (FR67), then each release as "checkout v2.1 at 02:11", or "checkout v2.1 first seen in telemetry 02:11", collapsing past 5 (*guess*) with a count. It is the legend for the rules and shading, and their keyboard and screen-reader form. Shading is paint on a canvas and cannot take focus, so what is at the cursor is named by the readout (FR32), and the table view adds a column for it.
- **FR48.** A condition affecting the whole page is said once, above the grid, in the design's order of priority, with at most one severe line at a time: "Could not read telemetry since 02:14. Notes and findings below are current", with one "Read again"; "Choose a service to fill this dashboard", holding the picker, where affected panels shrink to a dash; never reported: "Nothing has reported to staging yet", with how to start; reported before: "Nothing reported in this window. Last seen 3 hours ago", with "Show the last 24 hours". A page-wide read failure follows FR31: panels with an earlier reading keep their line for one interval first, so one failed batch never blanks a page. Words naming an action follow the reader's role (FR65).
- **FR49.** A panel's `width` is its share of a row, and the last panel in each row grows to fill it, so no saved layout leaves empty columns on the right. From `lg` up to `xl`, time panels and tables of width 3 or 4 span 6 columns. From `xl`, a table spans at least 4. "View as table" on a narrow chart opens at full row width. Below `lg`, every panel is full width. The builder and the preview show the real result.
- **FR50.** At 375 px there is no sideways scroll, header actions move into one menu while runbook links stay outside it, the service picker, the stale state and the "could not be read" count stay reachable while scrolling, and every touch control below `md` is at least 44 px, as the top bar and sidebar are (`TopBar.tsx:39-42`, `Sidebar.tsx:74`). A tap shows the value at that moment and on the other panels if T4.10 proves it works; "View as table" is always the route to exact values. A window can be changed without dragging, through the top bar's "Pick a range". Panels are drawn as they scroll into view.
- **FR52.** A dashboard's `default_range` is used when the address names no window, and does not change the reader's remembered choice. While a dashboard is open, the range list also offers 30 days, and raw panels with a lower limit say so (FR17). `default_range` of 30 days works the same way.
- **FR53.** A link's project and window, and a drag on a chart, apply to this page and are not remembered. The page says so: "Showing staging from this link. You were on production. Back to production", and for a fixed window, "Fixed time: 02:00 to 03:00. Back to live". "Fixed time" is the one name for this, in the top bar's label too, and there is one "Back to live", on the page line, which the top bar's control also acts on. A drag pushes one history entry, so browser Back returns to the earlier window, and the page line offers it: "Zoomed to 02:10 to 02:20. Back to 02:00 to 03:00". A drag's window is rounded out (T4.6), so colleagues zooming on one spike share scans. After a drag, only panels on screen are read, and the rest read as they scroll in. Following a panel's link to a service carries the window, because the person asked. A link to a project the reader cannot open shows the refusal, keeps the address, draws no numbers and leaves the remembered selection alone. A link's `service=` value that is not found in the project is shown quoted, truncated and marked as coming from the link.
- **FR54.** A live page whose newest telemetry is over 10 minutes old (*guess*) says "Newest telemetry here is 12 minutes old. The pipeline may be behind, or nothing is sending", while reads are succeeding. No panel reads as calm under it.
- **FR55.** A full-screen view hides the sidebar and top bar, and keeps the project line, the age, the stale banner, the paused chip and the "could not be read" count. A visible "Leave full screen" appears on focus or pointer movement, Tab moves focus to it, and Escape leaves. The page scrolls as normal, and docs recommend keeping a wall dashboard to one screenful.
- **FR56.** A dashboard has up to 5 links (*guess*), each a label and an `https` address, checked by the validator and drawn by the console with `rel="noopener noreferrer"`, with the destination's host beside the label. The validator refuses an address with a user name or password, and one whose query string holds a key matching the masking rule's credential markers (`mask/rules.go`).
- **FR57.** A finding's screen, a service's screen, and an alert incident's screen whose rule names a service (FR67) offer "Dashboards showing this service", opening with that service picked. Dashboards naming the service come first, then by last opened, at most 5, with "See all" when more exist. From an open finding or incident, the dashboard opens on a live window that starts before it began and refreshes: the shortest range that covers it. From a closed one, it opens on a fixed window with a margin on each side.
- **FR59.** After 3 seconds (*guess*) of a first read, the page says "Still reading. This can take up to 25 seconds", the real bound of the time budget. The header, notes, open findings and the "In this window" strip draw before scans finish.
- **FR60.** A failed or timed-out panel, a panel still refused after its retries, and a page state all show the batch's correlation id, selectable, so it can be quoted to support (`principles.md` rule 8). A batch the browser gave up on at 25 seconds has its own page state: "The page did not answer within 25 seconds", with "Read again".
- **FR61.** Invisible and direction-changing characters in every telemetry value (service, endpoint, error text, node, check, version and metric attribute) and in all stored free text (title, description, panel title, note, label) are shown as visible escapes such as `[U+202E]`, wherever they appear: picker, legends, tables, tooltips and sentences. The raw value is what is bound and stored. Titles, labels and panel titles refuse control and invisible characters and NUL at the validator, so a near-identical title cannot pass the unique index.
- **FR62.** Under a note, on a self-hosted install: "Everyone in this organization can read this. Do not paste secrets or personal details." On the hosted service: "Everyone in this organization can read this, and support while an owner has granted access. Do not paste secrets or personal details." Both add that an owner or admin can remove text at once (FR68). On export: "This file includes notes, links, service names and host names."
- **FR63.** The panel context carries detection's state, from the function behind `GET /projects/{id}/detection/status` (`detection/service.py:143`, Postgres only). When it is not `judging` with all services judged, the feed's own line (`detectionHeadlineFor`, moved to `shared/` with its types) shows above the grid. An `open_findings` panel then shows a dash and that reason, never 0, and an empty strip says the same, so "no findings" cannot read as "nothing wrong" while detection is learning, behind or stopped. This is also how an empty template says when marks will first appear.
- **FR64.** When any panel could not be read, a visible line in the header, and in the phone's sticky line, says "6 of 30 panels could not be read", as a link moving focus to the first such panel. It is hidden when all are read, so failures below the fold cannot go unseen.
- **FR65.** Words naming an action follow the reader's role. A viewer is never told to remove panels, rename a check or set anything up. For example: "This dashboard asks for more reads than one page may make. Ask someone who can edit it to remove panels", naming the owner. A support view is treated as a viewer.
- **FR66.** The newest bucket of a live window is still filling, and dividing its count by the whole bucket reads as traffic collapsing at the right edge (`services/service.py:73-80`, read, not run). A batch marks that bucket. The panel leaves it off the line and shows it in the readout and the table as "so far".
- **FR67.** Alert incidents whose rule names a service are listed in the "In this window" strip, from Postgres in the panel context, at most 50 (*guess*), with the cap stated. They draw no shading in this version. Threshold alerts link to the incident (`alerts/messages.py:128`), and a trial has no findings for 3 days, so this is the path most alerts take to a dashboard.
- **FR68.** An owner or admin may purge at once: a dashboard, or one named version. It hard-deletes that text and rewrites `title` and `previous_title` to `[removed]` in that dashboard's audit entries. One audit entry records it, holding no removed text. Backups are outside this path, and the setup page says so. This is the answer to a pasted secret or a person's erasure request, which an edit does not give, because old versions stay readable.
- **FR69.** A self-hosted operator's setting, or the hosted service's own, turns dashboard scans off. Panels then read "Dashboards paused by your administrator", no scan runs, and alerts keep running.
- **FR70.** A finding is judged from per-service readings, and a chart reads `endpoint_stats_1m`, so a number in a finding can differ from the chart beneath it. When the strip lists a finding, one line says so: "Findings are judged from their own readings, so a number in a finding can differ slightly from this chart."
- **FR71.** Anyone may set this tab's refresh to 30 s, 1 minute or 5 minutes, within the floor, unsaved. "Read now" reads the page at once through the shared read, so repeated presses cost nothing extra, and each panel's read time stays honest about how old its answer is.

### Anti-sprawl

- **FR36.** The list shows title, description, owner, panel count, last opened and last changed, and how many panels are not reading, from the last batch read that stamped last opened. It sorts by any of them through real header buttons, filters by title, and marks a dashboard not opened for 30 days (*guess*). On a phone, the row shows title and last opened, and the rest under it. List routes read stored columns only, never definitions.
- **FR37.** "Last opened" is the latest time anyone's batch read ran, to within an hour. A wall display therefore counts, and the screen claims only that the page was open, not that anyone watched it. A support view never moves it.
- **FR38.** A dashboard whose owner has left shows "Former member" and stays editable by anyone with the permission.
- **FR51.** Each save that changes the title or definition keeps the version it replaced, with who and when, and "Restore this version" is an ordinary save. A save changing nothing makes no version. A version is trimmed only when it is both past the newest 20 (*guess*) and older than 30 days (*guess*), up to a hard ceiling of 200 per dashboard (*guess*), so 20 quick saves by a compromised account cannot erase the last good version. Delete hides a dashboard for 30 days (*guess*), and the list's "Recently deleted" can restore it.
- **FR58.** The list's states are designed: loading, failed, empty for an editor (FR39), empty for a viewer ("No dashboards yet. Ask a member to create one"), and no results, with a way to clear the filter.

### Starting points, export and import

- **FR39.** The list's empty state offers *Start from Overview*, *Service health* and *Infrastructure*, then a blank dashboard, in that order. A template opens as an unsaved draft. A template already saved shows as "Open". The templates' exact panels are listed under *The templates*, below.
- **FR40.** *Start from Overview* holds the same panels and numbers as Overview's three charts, four totals (services, requests a minute, errors, p95: `OverviewScreen.tsx:186-193`) and worst services.
- **FR41.** A service's screen offers "Start a dashboard from this service", which opens *Service health* with the picker on that service. If *Service health* already exists, the action reads "Open Service health for checkout" and goes straight there.
- **FR42.** Export downloads the dashboard document as JSON, with no organization id, owner, dashboard id or row id. Panel ids stay, since they mean nothing outside the file. Checks and SLOs are stored by name, and a metric series by its hash, so a file holds no attribute values and works in another project or organization. A name with no match reads `not_in_project`. Import reads a file in the browser and sends it to a gated validate-only route that runs no scans (D53). Only if it passes does it open as an unsaved draft, either new or over an existing dashboard as a normal version-checked save. Import refuses a file over 64 KiB, with a newer `schema_version`, or failing FR7, and nothing is drawn from it. Viewers get no import.

### The templates

Exact panels, widths and scan counts. Scan counts are worst case and arithmetic. Each must show no `refused` panel for one person on the demo (AC129); if it cannot, its raw count falls.

| Template | Panels (width) | Scans |
|---|---|---|
| *Start from Overview* | `project_trend` rate, errors, latency (4 each); `project_number` service count, requests a minute, error rate, p95 (3 each); `services_table` worst, 10 rows (12) | 3, none raw |
| *Service health*, with the picker | `service_trend` rate, errors, latency (4 each); `service_number` p95, compared (3); `open_findings` for the service (3); `endpoints_table` slowest, 10 rows (6); `error_groups_table`, 10 rows (12) | 5, 1 raw |
| *Infrastructure* | `top_nodes_trend` count 5 on `system.cpu.utilization`, `system.memory.utilization`, `system.filesystem.usage` (4 each) | 3, all raw. The cap of 2 at once makes the third wait (*a guess that this fits the 5 s wait, unmeasured*). With no host metrics, each says "No machine has reported this metric in this window", with a setup link for those who can act |

## Non-functional requirements

- **NFR1. Isolation.** Every Postgres read binds `org_id`. Telemetry binds the tenant from the session (`auth/dependencies.py:246-257`). An unknown and a foreign id answer with the same 404.
- **NFR2. No runtime SQL, and the project is bound.** Every new ClickHouse statement is a module constant that binds the tenant and the project, in every `SELECT` over a telemetry table, subqueries included. A dashboard is shown against one project, so this is the boundary customers rely on inside an organization.
- **NFR3. Load.** A dashboard at the scan cap and default refresh must spend at most 40 of a person's 180 scans a minute, or 40 of 90 at two API processes (arithmetic, not a measurement). At the 30-second floor that is 80, which still fits both.
- **NFR4. Speed.** A 30-panel dashboard's first read should complete within 3 seconds on the demo at a 1-hour window. **This is a target for T5.7, a guess, and is not promised.** The hard bound is that a read answers within its deadline (AC19).
- **NFR5. Accessibility.** Every builder action works by keyboard, focus is kept after a move, moves are announced, every chart has a table view, each panel is a `<section>` labelled by an `<h2>` holding its title, as Overview's are (`OverviewScreen.tsx:227-228`), and a single live region reports "28 of 30 panels read" on change only, never on each refresh.
- **NFR6. The window.** The grid uses the whole window, with no capped containers and no empty columns on the right (founder's standing rule, FR49).
- **NFR7. Existing screens unchanged.** Chart changes are additive. Every screen that uses `TrendChart` looks as it does today.
- **NFR8. AI off.** Nothing here needs `scheduler-insight`, and no dashboard module reaches any item on the repository's one deny-list of AI modules and provider SDKs. A test holds this.
- **NFR9. Deletion.** `dashboards` and `dashboard_versions` join `organizations/tables.py`, or `test_deletion_live.py` fails.
- **NFR10. Support view.** A support view can read a dashboard and change nothing. It moves no "last opened".
- **NFR11. Alerts and ingest come first.** With 20 worst-case dashboards running, `nexovue_scheduler_detection_settled_minute_age_seconds`, `nexovue_scheduler_oldest_alert_waiting_seconds`, `nexovue_scheduler_oldest_ready_job_seconds` per queue, `nexovue_worker_consumer_lag_records` and a named failed-insert measure (`nexovue_worker_dead_letter_records` at least) must stay within numeric margins committed before the run, compared with no load, at 1 and 2 API processes, on a stated machine, ClickHouse ceiling and data volume. A result is from the demo and must say so. If this does not hold, F20 does not ship at those caps.
- **NFR12. A team on one page.** 30 people on one 30-panel dashboard at 1-minute refresh must produce no refused panels on the demo (*guess until measured*), and 10 people each zooming 3 times a minute must leave all panels on screen `ok`. Panels off screen may wait.
- **NFR13. Nothing leaves.** Dashboards open no outbound connection, and the setup page says so. A test holds this for direct imports in both packages.
- **NFR14. A bound outside the API.** The API reads ClickHouse through its own account, `nexovue_api_reader`, with `max_memory_usage_for_user` well below the server's roughly 4.8 GB cap and a `max_concurrent_queries_for_user` limit, and the scheduler stays on `nexovue_reader`, outside it. So API reads at their worst cannot starve the alert and detection reads or the inserts. Setting names read from ClickHouse's `Settings.cpp`, 2026-11-02, master branch and not the pinned 26.3 tag; values are *guesses* set by T5.7.

## Error cases

| What goes wrong | What the person sees | Can they recover? | Told apart from normal empty? |
|---|---|---|---|
| One panel's scan fails | That panel: "Could not read this" with the correlation id, and read again. Old line only under FR31's strip | Read again, or the next refresh | Yes: `failed` against `empty` |
| Over quota partway through a batch | Lowest panels: "Too many reads just now. Reading again in 20 s". Others draw | Automatic (FR45) | Yes: `refused` |
| A scan outruns the read's deadline | Those panels: "The database took too long to answer" | Next refresh, or read again | Yes: `timed_out` |
| Stored dashboard over a lowered scan cap | Panels past it, for an editor: "This dashboard asks for more reads than one page may make. Remove N panels". For a viewer: "... Ask Asha, or someone who can edit it, to remove panels" | An editor removes panels | Yes: `over_limit` |
| Whole request refused | FR45's sentence, "Too many reads just now", at page level, with no toast; last readings stay on screen, marked | Automatic, honouring `Retry-After` | Yes |
| ClickHouse down | One line above the grid. Notes, open findings and deploys still draw, including when the API has never connected | When it returns | Yes: never zeroes |
| Reads succeed, telemetry has stopped | FR54's line. Panels draw what they have | When the pipeline recovers | Yes, by the sentence |
| Service not in this project | "No record of checkout in staging" | Pick another project | Yes: `not_in_project` |
| Two checks share the name | For an editor with the right: "Two checks in staging are named Login. Rename one". For a viewer: "Two checks in staging are named Login, so this panel cannot tell which to read" | Rename a check | Yes: `ambiguous` |
| Service quiet in this window | "No requests in this window. Last seen 3 hours ago" | Widen the window | Yes: `empty` |
| Window older than the data | The line starts late, and the panel says "Data starts at 14 Oct", or "Your plan keeps 30 days" where a plan explains it | Narrow the window | Yes, by the sentence |
| Window too wide for a raw panel | "This chart can show at most 7 days", and a button showing the last 7 days | Press it | Yes |
| Picker has no service chosen | FR48's line, with the picker in it | Choose one | Yes: `needs_service` |
| Link names a project I cannot see, or a deleted one | "This link is for a project you cannot open", with a way to view against mine | Yes | Yes |
| Dashboard deleted while I have it open | "This dashboard was deleted", with restore for an editor and a link to the list | Restore | Yes |
| Link to a dashboard that does not exist | "This dashboard does not exist or is no longer available". The same for a foreign id (NFR1) | Go to the list | Yes |
| Someone else saved first | "Asha saved this 2 minutes ago", reload or save as a copy. Draft is kept | Yes | N/A |
| Save refused by the check | Inline beside the panel, and a summary | Fix it | N/A |
| Import is not a valid file | What is wrong with it, in words, from the validate-only route. Nothing is opened or drawn | Fix the file | N/A |
| Viewer reaches `/edit` by a link | The existing refusal (`Permitted.tsx`), and a link to the dashboard | Ask an editor | N/A |
| Network lost on a wall display | The console's offline banner, then the stale banner (FR31) | When it returns | Yes |
| Session expired on a wall display | The console's normal sign-in page, which remembers the page (`query-client.ts`). **A blank wall is possible; this is the known limit** | Sign in | Yes |
| Detection not judging | Detection's own line above the grid; `open_findings` shows a dash and why, never 0 | When detection runs or finishes learning | Yes |
| A chosen metric series changed its attributes | "orders_processed still reports, under different attributes. Choose again" | Choose again | Yes: `series_changed` |
| Too many series to combine | "This metric has more than 1,000 series, too many to combine. Choose one series" | Choose one | Yes: `too_many_series` |
| Browser gave up at 25 seconds | "The page did not answer within 25 seconds", with "Read again" | Read again | Yes |
| Scans turned off by the operator | "Dashboards paused by your administrator". Notes and findings still draw | When turned on | Yes: `paused_by_operator` |

## Acceptance criteria

**Front door**

- **AC1.** Opening `/` must render the Findings feed, and `/overview` must render Overview with the numbers it shows today.
- **AC2.** The sidebar must offer both, `route-manifest.test.ts` must pass, and no link in the console or in a scheduler message may point at the removed `/findings` list address, including `alerts/messages.py:378`.
- **AC3.** A project with no findings and with telemetry must show the summary, Overview's three charts with no extra scan, and a link to Overview on `/`. A project still learning must also say when judging starts. A project answering `service_count` 0, in the API's real shape, must show "Nothing has reported in this window", no zero readings, no charts, a link to setup and advice to widen the window. Neither may be blank.
- **AC54.** Every link each scheduler message composes must match a route in the checked-in route list, and `route-manifest.test.ts` must hold that list to the router.
- **AC55.** On `/findings/$findingId`, Findings must be the active sidebar item, in a real render of the sidebar. The 404 screen's link must say "Back to Findings".
**Definitions and rights**

- **AC4.** A save or preview with an unknown field, unknown kind, a setting outside the allowed values table, 31 panels, 65 KiB or a worst case of 41 scans must be refused with a reason naming the panel or the count.
- **AC5.** A file with a panel holding `"sql"`, or a kind called `query`, must be refused.
- **AC6.** Reading, changing or deleting another organization's dashboard by id must answer exactly as a non-existent id does.
- **AC7.** A viewer must be refused every write, and the console must offer none. A viewer's empty list must offer nothing that would fail. Owner, admin and member must succeed.
- **AC8.** A support view must read a dashboard, must be refused every write including preview, and must not change `last_viewed_at`.
- **AC9.** An API token of each kind (all-project, one-project and, once F4 has merged, an assistant ticket) must be refused every dashboard route through the real app, including `/dashboard-panels/read`, `/dashboard-panels/preview`, `/metrics/{name}/series`, `context`, `templates` and `validate`, spending no scan. None may appear in the read API's published description. A structural test must hold every such handler to `CurrentIdentity`, `MayEditDashboards` or `MayPurgeDashboards`.
- **AC10.** Two saves from one version: the first must succeed, the second must get a conflict, and its draft must remain on screen.
- **AC11.** A second dashboard with the title "checkout" when "Checkout" exists must be refused, naming the existing one. A deleted dashboard's title may be reused, and restoring it must then ask for a new one.
- **AC12.** Deleting an organization must remove its dashboards, versions and deleted dashboards, and leave another organization's untouched (`test_deletion_live.py`).
- **AC13.** Each create, change, delete, restore and purge must write one audit entry with title, panel count, version and the definition's hash, and no entry may contain the definition. A rename must record the old title. A purge entry must hold no removed text.
- **AC56.** After a bad save, "Restore this version" must bring the earlier definition back as a new version. A version older than 30 days and past the newest 20 must be trimmed by the save. A version newer than 30 days must not.
- **AC57.** A deleted dashboard must leave the list, a link to it must say so, and restoring it within 30 days must bring it back whole. The expiry sweep must remove it and its versions at 31 days and leave it at 29, in a live test.

**Reading**

- **AC14.** Over a fixed window ending before seeded data stops, a panel's request and error counts must equal, to the digit, the values from the matching route. A latency percentile must match within the tolerance recorded by T2.8's live check: read a window with over 8,192 samples twice through one route. A fixture below that size must match to the digit. Kinds with no route of their own are checked another way: each `top_services_trend` and `top_nodes_trend` line must equal that service's or node's trend; a compared value must equal the route read over the previous window; `open_findings` must equal the feed's count; `endpoint_trend` must equal the endpoint row's counts over the window; `check_trend` must equal that check's series from `GET /synthetics/checks/trend`.
- **AC15.** From an empty shared-read store, three `service_trend` panels on one service must run one scan and spend one quota place.
- **AC16.** From an empty shared-read store, a batch of N distinct scans must spend exactly N quota places in both person and organization buckets, the same keys the middleware charged, and run at most 4 at once.
- **AC17.** From an empty shared-read store, when a person has 3 quota places left, a batch of 10 distinct scans must return 200, with the first panels `ok`, the rest `refused`, and a wait time.
- **AC18.** The request's slot and every process-cap place must be released after success, failure, refusal and client disconnect. Through a real server with a client that really disconnects: unstarted scans must not start, and what ClickHouse does with a running statement must be recorded.
- **AC19.** A batch must never spend more than its deadline, counted from the request's arrival: when it passes, unstarted and running panels must be `timed_out`, and the response must return 200 inside the console's client timeout, carrying the panels that finished. `refused` must never be used for a slow read. Each path must be tested, including a live one where ClickHouse's own limit fires first.
- **AC20.** A failing scan must leave other panels `ok`, and the API's `failed` result must carry no series. A panel with no earlier reading must show words and no chart. A panel with an earlier reading must show it under the warning strip for at most one interval, then words alone. Each must be tested for `failed` and for `refused`.
- **AC21.** Through the ingest path and the usage meter, a service that has never reported to the displayed project must read `not_in_project`. One that has, and is quiet in the window, must read `empty`, with when it was last seen.
- **AC22.** A `check_trend` panel naming a check that is not in the displayed project must read `not_in_project`, and must not reveal any other project's checks. Two checks of that name must read `ambiguous`.
- **AC23.** A raw panel over a window above its limit must read `window_too_wide`, run no scan, and leave others unaffected. A 30-day `check_trend` must draw.
- **AC24.** `GET /dashboard-panels/read` must reach the batch route, and `GET /dashboards/templates` must reach the templates and not `/dashboards/{id}`, through the real app.
- **AC25.** Every new ClickHouse router must be on the telemetry tier and every save on the default tier (`test_ratelimit_coverage.py`).
- **AC26.** A `top_services_trend` or `top_nodes_trend` panel must choose its lines in the store, not by trimming afterward, and must return at most 5. With two projects, where the other project's services would rank higher, it must choose only from the displayed project.
- **AC58.** A `node_trend` panel for a host late in the alphabet, on a fleet over the old row cap, must draw, and must run one statement bound to that node.
- **AC59.** A read over the API must keep a process to 5 dashboard scans and 2 raw ones at once, whichever requests they belong to, including a task-owned read whose requester has gone.
- **AC60.** Limits off (`rate_limit_enabled` false) must charge nothing and refuse no panel.
- **AC61.** With the API started while ClickHouse is down: the batch must answer 503, the context must answer 200, and the page must draw notes, open findings and one line saying telemetry could not be read. The console must make one batch request per interval, with no retry on that 503 and no toast.
- **AC62.** A second person reading the same panels within 30 seconds must spend no scan, and the panel's read time must be when it was really read. No read may return another organization's or project's kept answer.
- **AC63.** A statement whose readers have all gone must end on the server within its fixed limit of 15 s (*guess*) plus a 1 s margin, and sooner if the cancel setting works, checked in `system.processes` with a slow statement through the test seam.

**Viewing**

- **AC27.** A link naming project B must show project B to a reader whose selection was project A, and the top bar and header must both say project B. No read may reach project A first.
- **AC28.** A "this moment" link over a settled window must show identical numbers on a second reading, and must not refresh. A window ending in recent minutes must say those minutes can still change.
- **AC29.** Two dashboards must refresh at their own settings, and a refresh must not flash the skeleton. A hidden tab must make no reads.
- **AC30.** After three missed refresh intervals, or 5 minutes if sooner, the banner must show and name the time of the last good reading. With ClickHouse down and reads answering 200, the page must never say "updated just now". Refresh off must say so at all times.
- **AC31.** The data half: synced options must be set, and the readout must format a single-series panel's value in its unit. The drawing half, in a browser: moving the cursor on one time panel must show values at that moment on all, and one drag must produce exactly one window change, one address change and one batch read.
- **AC32.** The data half: a deploy inside the window must be in the payload and in the options for its service's panels, project panels and project-wide kinds, including when more than 50 deploys are newer than it. The drawing half: a browser check must see the rule. Over the cap, the screen must say so.
- **AC33.** The data half: a finding open during the window, and a project-wide one, must give spans for the right panels, and a busy window must state its cap. The drawing half: a browser check must see the shading.
- **AC34.** Every chart must offer a table holding the same numbers.
- **AC35.** A value `<img src=x onerror=alert(1)>` as a service, endpoint, error text, node, check, version and metric attribute must appear as those literal characters in every title, legend, table, note and tooltip, and nothing may run. A render test in `just check` covers what jsdom can see. The chart's canvas labels are proved only in the browser run.
- **AC36.** A note holding Markdown, HTML or a URL must display it as literal text, with no link created.
- **AC64.** With a browser zone different from the project's, axis, readout, sentences, top bar window phrase, paused chip and range picker must all be on the project's clock, and the header must name it. A range entered in the picker must read that hour in that zone.
- **AC65.** From a dashboard with an open finding, a keyboard user must reach the finding in one action from the "In this window" strip, with the window kept.
- **AC66.** A panel's service link must be reachable by keyboard and must keep the window. A service with no deploy reports must say so on its panel.
- **AC67.** At 1280, 1920 and 2560 px, no row of any saved layout may leave empty columns on the right. At 1024 px, a chart and a table must each be at least 6 columns wide. At `xl`, a table must be at least 4. A time panel saved `small` must be refused. "View as table" must open at full row width.
- **AC68.** At 375 px, in a Playwright run, there must be no sideways scroll, and the picker, stale state and "could not be read" count must stay reachable while scrolling. Exact values must be reachable by touch through a table.
- **AC69.** After leaving a dashboard opened by a link or dragged to a time, and on reload elsewhere, `/overview` must show the reader's own project and window. A link to a foreign or deleted project must show the refusal, keep the address, draw no numbers and leave `nexovue.selection` unchanged.
- **AC70.** With ingest stopped and ClickHouse up, FR54's line must show within its threshold.
- **AC71.** On a fixed window with refresh off, a refused panel must fill in by itself once quota returns, reading only refused panels, at most 3 times, then offer "Read again". Paused, it must offer "Read again".
- **AC72.** With one scan stuck, at 5 seconds the page must show its header, notes and "Still reading".
- **AC73.** A compared number with no earlier window, or one only partly covered, must show "Not enough history to compare" and no arrow. A single reading must be visible as a dot. Tested with a service 10 minutes old and a half-covered previous window.
- **AC74.** A value holding a right-to-left override must show a visible escape in the picker, legends, tables, tooltips and sentences, parametrized over service, endpoint, error text, node, check, version and metric attribute. A named Playwright spec must prove this on Chromium. On phone Safari it must be a hand check, recorded with device and date, because the repository's Playwright has one browser, Desktop Chrome, and a WebKit project would still not be phone Safari.
- **AC75.** Dashboard and project 404s must each reach their own page state through the real status path, and a foreign id must answer as an unknown one does.
- **AC76.** Full-screen view must hide the sidebar and top bar and keep the age, banner, paused chip and "could not be read" count. Escape and a visible "Leave full screen", reachable by Tab, must leave it.
- **AC77.** A link with a `javascript:` address must be refused on save and on import.
**Builder**

- **AC37.** Every builder action must work with the keyboard alone: one tab stop per panel's toolbar, focus kept on the moved panel and at the ends, focus placed after add and remove, every change announced in the live region, and Remove undoable. A real screen reader pass is recorded separately (`plan.md`, *What these tests cannot prove*).
- **AC38.** A template, once opened, must not create a row until saved.
- **AC39.** *Start from Overview*, saved, must show the same three charts, four totals and worst services as Overview, counts to the digit and latency within AC14's tolerance, over a fixed window. Held until Step 5 builds its number and table panels.
- **AC40.** Export then import must give an equal dashboard document, and the file must hold no organization id, owner, dashboard id or row id. Imported into a second organization, a `check_trend` panel must resolve by name, or read `not_in_project`.
- **AC41.** Importing a file with a newer `schema_version` must refuse it plainly.
- **AC42.** Saved panels of a kind removed from the code must show the "no longer available" message, and the rest of the page must draw.
- **AC43.** With no new option given, `TrendChart`'s options must equal today's, and a named screenshot comparison in Design QA must show every screen using it unchanged.
- **AC44.** With the service picker on, one dashboard must show two services by changing only the address, and no new row may be created.
- **AC78.** Duplicating a dashboard must ask for a new title and show the existing ones. The 1,001st live dashboard must be refused, saying why, and the 2,001st counting deleted ones. A restore at the live cap must be refused with words. A saved draft must equal what was previewed.
- **AC79.** A definition over the cap at read time must give `over_limit` for panels past it, and never a retry promise.
- **AC80.** Each template must hold exactly the panels listed under *The templates*, must pass the validator, and must draw on a fresh demo project.
**List**

- **AC45.** Last opened must move on a batch read, at most once an hour. A dashboard not opened for 30 days must be marked.
- **AC46.** Sorting by last opened must place never-opened dashboards first in the "least opened" order, and each sort header must carry `aria-sort`.
- **AC47.** After its owner's removal through `members.service`, a dashboard must remain, read "Former member" and stay editable.
- **AC81.** The list's title filter, its no-results state and each empty state in FR58 must be shown as written.
- **AC82.** "Dashboards showing this service" must list a dashboard naming that service and one using the picker, and must open it with that service picked. With six picker dashboards and one naming the service, the naming one must come first and "See all" must show.

**Metric series (later step)**

- **AC48.** A `metric_series` panel of a cumulative counter must equal the counter's real rise, within rounding, against a value computed in the test from a fixture it writes itself. The demo is the founder's judging step only.
- **AC49.** A restart inside the window, in that fixture, must not produce a spike.
- **AC50.** Two instances of one service reporting equal attributes must be treated as two series.
- **AC51.** A metric reporting two units in the window must say so, and must not add them.
- **AC52.** A `metric_series` panel on all series must draw one line and state how many it combined. Over 1,000 series (*guess*), it must read `too_many_series` and draw nothing.
- **AC83.** Two label values of one metric must draw as separately chosen panels, through `GET /metrics/{metric_name}/series`.

**Added by the second review**

*Time, shared reads and limits*

- **AC84.** The middleware's slot waits have two outcomes, and each must be tested. (a) When the person's slot frees just before 5 s, and the organization's just before the next 5 s, the middleware waits about 10 s and the handler runs: the batch must answer 200 with partial panels inside the client's 25 s timeout. So must a batch whose Postgres pre-read waited on the pool. (b) When a bucket stays full through its own 5 s wait, the request must get a 429 at the end of that wait and the handler must not run: about 5 s if the person's bucket is the full one, in which case the organization's is never tried, and up to about 10 s if the organization's is. The page must then show FR45's sentence (AC145).
- **AC85.** A structure test must fail if any statement in F20's named list omits `project_id = {project_id:String}` from a `SELECT` over a telemetry table, subqueries included.
- **AC86.** Ten concurrent misses for one key must run one statement and charge one reader.
- **AC87.** A leader refused on its quota or process cap must create no shared entry. A joiner with quota left must run its own read and must not inherit `refused`.
- **AC88.** A leader 14 s into its budget and a joiner 2 s into its own must both be served by a statement with the fixed limit, and the joiner must not inherit a timeout from the leader's deadline.
- **AC89.** A leader that disconnects with a joiner present must leave the joiner an answer or a truthful state. With no reader left, the statement must be cancelled. A task-owned read must hold its process-cap place until it ends.
- **AC90.** Filled to its cap, the shared-read store must retain at most 100,000 rows in total and 25,000 for one organization (*guesses*), and an answer of over 2,000 rows must not be kept. Expired entries must go without waiting for eviction. T5.7's report must state peak API memory with the store full.
- **AC91.** With API reads saturated, `nexovue_api_reader` must hit its server-side limits while inserts and the detection sweep continue, in T5.7. A `TOO_MANY_SIMULTANEOUS_QUERIES` error must read `refused`, not `failed`. A compose contract test must hold that the API's DSN uses that account and the scheduler's does not.
- **AC92.** With under 1 s left, no statement may start and the panel must read `timed_out`. ClickHouse's `TIMEOUT_EXCEEDED` must map to `timed_out` ahead of the broad catch.
- **AC93.** A test must hold `READ_SETTINGS` and `DETECTION_READ_SETTINGS` unchanged by F20. `cancel_http_readonly_queries_on_client_close` must be sent only by a `bounded_by` repository.
- **AC94.** On a hosted install, one organization saturating its raw reads must leave another organization's raw panels `ok`, and its top panels `ok` at its limits. On a single-organization install, that organization must be able to use the whole cap.
- **AC95.** A `check_trend` panel must run one statement bound to one `check_id`, resolved in the pre-read.
- **AC96.** A list route must read `panel_count`, `service_names`, `uses_picker` and the broken-panel count from columns, and must read no definition.
- **AC116.** With the operator's setting off, panels must read `paused_by_operator`, no scan may run, and notes and findings must draw.

*What the page says*

- **AC97.** With detection stopped, an `open_findings` panel must show a dash and the reason, never 0, and the page must show detection's line. The same for learning, with its date, and for behind.
- **AC98.** When all panels fail and have earlier readings, lines must stay for one interval, the page line must say so once, and no layout shift may happen while the failure continues. Panels must shrink once, and regrow only after a successful read.
- **AC99.** A service-scoped errors table on a service that sent nothing in the window must say "checkout sent nothing in this window. Last seen 3 hours ago", and "No errors in this window" only when the service was seen in it. A `failing_only` dependencies table with no failures must say "No dependency failed in this window".
- **AC100.** At 375 px and 1280 px, with failed panels below the fold, "6 of 30 panels could not be read" must be visible, and its link must move focus to the first. It must be hidden when all are read.
- **AC101.** At 375 x 667 with every page condition active, the top of the first panel must be visible without scrolling, and at most one severe line may show.
- **AC102.** From an open finding, a dashboard must open on a live window that started before it and refreshes. From a closed finding, on a fixed window with a margin on each side.
- **AC103.** A drag then browser Back must restore the link's window with one read, and the page line must offer the same.
- **AC104.** One test per row of the design's refresh state table. The banner must not fire when paused, fixed or off, and on a returned tab must wait for the first read. In a fixed window the control must show no Pause and no interval.
- **AC105.** A viewer and a support view must be offered no action they cannot take, in every panel and page state that names one.
- **AC106.** On a multi-line chart, the keyboard must reach each name, activating one must open the service, and no line may be hidden by it.
- **AC107.** The readout must name the finding and release at the cursor, the table view must add that column. Where T4.10 records that touch works, a tap on a phone must show the same (FR50); otherwise the table column is the required route.
- **AC108.** Every touch control below `md` must be at least 44 px.
- **AC109.** A project that has never reported must say "Nothing has reported to staging yet", with setup. One that has, and is quiet in the window, must say "Nothing reported in this window. Last seen 3 hours ago", with "Show the last 24 hours".
- **AC110.** On a fixed window, a failed read must keep the line with no time limit under "Could not read again. This chart is from 02:40 and covers a closed period".
- **AC111.** `timed_out`, a `refused` past its retries and each page state must show the correlation id. A 25 s client timeout must reach its own page state. Each panel must be a `<section>` labelled by an `<h2>`.
- **AC112.** The side sheet must scale with the window, and a preview narrower than the panel's real width must say so.
- **AC113.** Every latency reading in a table header and readout must be marked as an estimate. Where the strip lists a finding, FR70's line must show. T5.7 must compare one finding's window against the chart and record the difference.
- **AC114.** A steady fixture must show no dip at the right edge at 1-hour and 24-hour ranges, and the newest bucket must read "so far".
- **AC115.** A metric gaining an attribute must read `series_changed` on a panel holding the old hash. A statement or node that has gone must say so in its own words. The list must show the count of panels not reading.
- **AC117.** An alert incident whose rule names a service must appear in the strip, and its screen must offer "Dashboards showing this service", with its window.
- **AC118.** A tab's own interval must not save, and "Read now" pressed twice inside 30 seconds must spend at most one scan per key.
- **AC119.** A note on a self-hosted install must not mention support. A hosted install's must say only while an owner has granted access. Both must mention purge. The export warning must name links.
- **AC122.** A `seen` marker must never read as reported, in the strip, the readout or the rule's style. `version` must follow FR61.

*Safety*

- **AC120.** After a purge, the text must be unreadable through every route, the audit entries must hold `[removed]`, one entry must record the purge with no removed text, and a member and a viewer must be refused.
- **AC121.** `GET /metrics/{metric_name}/series` must bind one service and a window of at most 7 days, return at most 200 combinations (*guess*), run under raw-read limits, refuse a token, and show each attribute through FR61. A saved definition and an export must hold only the hash.
- **AC123.** A title, label or panel title holding a control or invisible character or NUL must be refused. A link's unknown `service=` must show quoted, truncated and marked.
- **AC124.** A link with a user name or password, or with a credential-shaped query key, must be refused. More than 5 links must be refused. The console must draw the host beside the label and `rel="noopener noreferrer"`. The audit entry must hold hosts only.
- **AC125.** A save changing nothing must make no version, and 20 quick saves must leave the earlier good version restorable.
- **AC128.** A file holding `sql`, a `query` kind, a `javascript:` link or too many scans must be refused by the validate-only route with nothing opened or drawn, and a viewer must be refused that route.
- **AC142.** A structural test must hold direct imports in `dashboards` and `dashboardpanels` to no outbound client, and a browser check must see that opening and editing a dashboard makes requests only to the install's own address.

*Gates and gaps*

- **AC126.** T5.7's report must name each NFR11 metric with its baseline and loaded value, against margins committed beforehand, at 1 and 2 API processes, with machine, ClickHouse ceiling and data volume stated, and must say that demo volume does not prove customer volume and what must trigger a rerun.
- **AC127.** T5.7 must run NFR12's two cases and the incident script, and the first read's latency against NFR4's target.
- **AC129.** Each template opened by one person on the demo must show no `refused` panel. *Infrastructure* with no host metrics must say so, with setup.
- **AC130.** Two requests within one rounded minute must produce the same `window_start` and `window_end`.
- **AC131.** A window older than the data must show "Data starts at <date>" from the first row read, on a self-hosted install and on a plan-limited one, which adds "Your plan keeps 30 days".
- **AC132.** A node, check, statement or metric panel must say "All findings and releases in this project". A service with coverage `none` must say F28's words.
- **AC133.** An unsaved change must warn before leaving, and a change undone by hand must not.
- **AC134.** The side sheet must offer real values, keep a raw value behind an escaped label, and accept a typed value when the list has failed.
- **AC135.** Apply must preview that one panel, inside the sheet, through one read, and typing must make none.
- **AC136.** Delete must state the title, panel count and last opened, and that it can be restored for 30 days.
- **AC137.** Copy link must offer both choices, exact times first after a drag, and must confirm which was copied.
- **AC138.** When *Service health* exists, a service's screen must read "Open Service health for checkout" and go there, with no second copy.
- **AC139.** A 65 KiB file must be refused on import.
- **AC140.** An unset picker must show the page line holding the picker, `needs_service` on affected panels, and no scan.
- **AC141.** `default_range` must apply with no window in the address, must not change the remembered choice, and an unknown value must be refused. The API's list and `selection.ts` must agree, by test.
- **AC143.** The live region must say "28 of 30 panels read" on change only, and there must be only one.
- **AC144.** Drag must not ship unless T3.7's trial is recorded as passed, and the buttons must remain.

*Whole-request refusal*

- **AC145.** When the batch read itself answers 429, the page must show FR45's sentence with the correlation id, must fire no toast, must keep last readings under FR31's rule, and must not read again sooner than `Retry-After` says. Other screens keep the central toast.

**AI**

- **AC53.** No module of this feature may reach any item on the repository's one deny-list of AI modules and provider SDKs, without an exemption, and a control must prove the walk finds one from `nexovue_api.assistant.server`. Once F4 has merged, no handler from `dashboards` or `dashboardpanels` may be in F4's `TICKET_HANDLERS` (`ai-chatbot/design.md`, *The ticket*). With the AI role stopped, every criterion here must still pass.

## Dependencies and constraints

- **F6 must merge first** (foundation section 9). Findings exist only on this branch.
- **F19** gives the `slo_budget` panel. Its budget read is Postgres only, and its burn line is a separate function this panel does not call, and its SLO name is unique per project (`slo-tracking/design.md`, *Data and migration impact*).
- **F28** merges first and gives `markers_in_window` (Postgres only, fields `release_id`, `period_id`, `deploy_id`, `service_name`, `version`, `at`, `basis`, `state`) and `coverage_of` (F28 `design.md`, *Data flow and interfaces*, `spec.md` D30). It is a precondition of T5.4. D58 moves only Step 1, which needs no markers.
- **The founder's recorded decisions kept:** the feed is the front door (`feature-catalog.md:325`); insights and dashboards are both first-class (`vision.md:236`); no pricing by person (`vision.md:152`); AI is off until turned on and detection works without it (`phase-2-plan.md`).
- **Phase 2's AI safety rules:** this feature adds no AI link, so it adds no injection suite. Every rule still holds: dashboard text is free text for any later reader (BR11).
- **Sidebar and router are held together** by `route-manifest.test.ts`, and `NavPath` is a closed list (`route-manifest.ts:18-34`).
- **A correction to the code map:** both Overview and Findings comments say a fifth read is refused (`overview/api.ts:143-151`, `OverviewScreen.tsx:48-51`, `FindingsScreen.tsx`, `findings/api.ts:241-244`). The cap is 8, and an excess read waits (`policy.py:92`, `middleware/ratelimit.py:67`). Those comments are false. Removing the waits is a separate change, because at two API processes the cap is 4.
- **`service_last_seen` was checked by reading.** The usage meter writes a row for every service in a flush, joined to its project (`usage/postgres.go:166-175`), and detection already relies on it (`detection/projects.py:35`). A service that has just started can read `not_in_project` until the next flush if it also has no rows, and so can any service while the meter is stopped (`worker/run.go:291,307-311`), which is why the words claim only "No record". No query was run. `tools/detection-seed` writes no such row, so a seeded quiet service would read wrongly: AC21 must go through ingest, and the demo project (T11.4) must write it.
- **One check was run** before the second review, with a throwaway FastAPI app in memory: a `panels:read` colon route answers 422 when the `/dashboards/{id}` router is first. That is why D41 moved the route away from `/dashboards` altogether.

**Evidence, plainly.** No buyer has been asked. The case for a builder rests on peers having one and on one unverifiable page ([scope research](../../research/phase-2-scope-2026-10-30.md)). Competitor facts are from pages read on 2026-11-02 through a summarizing tool ([research](research.md), section 4, including the facts added in review, marked and with sources), and must be checked again before any public claim (T11.5).

### How far back a dashboard can look

From the seed in `0005_plans_table.py:34-37`. A search of later migrations found no change to these rows. Self-hosted installs have no plan allowance (`deployment.py`). Rollup panels read `endpoint_stats_1m` (`services/queries.py:75`), which keeps the plan's derived retention (`000008_endpoint_stats_1m.up.sql:27`), so Free's 30 days is the real limit on a compared number over 30 days. `service_stats_1m` keeps 35 days, but only detection reads it (`000035_service_stats_1m.up.sql:31-32`), so that limit does not reach a panel. Raw panels have the lower limits of FR17, whatever the plan.

| Plan | Spans and logs | Metrics and rollups |
|---|---|---|
| Free | 7 days | 30 days |
| Pro | 30 days | 395 days |
| Scale, Enterprise | 90 days | 395 days |

### What this brings, stated honestly

- **Positioning, for the founder to approve (founder decision 4):** "Findings tell you what broke. Dashboards hold the few numbers your team chose to watch, and start from a template, never a blank page." Docs say plainly that panels come from a fixed list.
- **Sales line, once built, and only then, labelled unproven by buyers:** "Findings from the detector, each linking to its evidence and its feedback, shaded on your time charts, next to the deploys you send us, in an AGPL product you run yourself. As many dashboards as you need, up to a safety cap we state." The cap is stated only after T5.7 sets it, and so is how many people and screens can read at once. **This is what nexovue does, and it is not offered as a difference** (foundation section 12): automatic anomaly marks on charts are not new (Netdata's ribbon, Datadog's `anomalies()`), and deploys on charts are parity (Datadog's Change Overlays). No free edition's findings-on-charts has been tested head to head. The wording goes into foundation section 12's future-tense list.
- **A reassurance, not a headline, for the founder to approve (founder decision 4):** "Dashboards cost nothing extra, on any plan, and we never price by person." Self-hosted is free, as Grafana's open-source edition is. The price differs only from hosted Grafana Cloud, and that comparison is internal only (foundation section 12 bans naming rivals in public). Named facts, from pages read: SigNoz says "No user-based pricing", Honeycomb lists "Unlimited seats", Datadog prices by host, and Grafana Cloud charges per active user.
- **Must not be claimed:** "better than Grafana" (foundation section 12); that findings on charts are unique; "the only" anything; "on every chart", since numbers and tables carry no marks; that a mark on a node, check, statement or metric chart means that chart caused or suffered it; "automatic deploy detection"; that a seen version is a deploy; that last opened shows anyone looked, or that free last-opened is a difference; drag, until T3.7 settles it; "exact" latency.
- **Rivals plainly ahead:** Grafana on everything flexible. Datadog, SigNoz, Sentry, Grafana and OpenObserve on AI building dashboards. SigNoz on migration from Grafana, Datadog and New Relic: an import of Grafana exports, a Datadog tool, and guides. Grafana, Datadog and SigNoz's Cloud and Self-Hosted Enterprise on public links; SigNoz Community has none (its page), and Sentry's page shows none. Dashboards as code (Grafana, SigNoz). General variables (Grafana, Datadog, SigNoz, Sentry). Kiosk or TV modes more complete than this full-screen view (Grafana, Datadog). Datadog on anomaly bands on graphs, and on deploys and other changes overlaid from one toggle. Netdata on anomaly marks on every chart on a free self-hosted agent. Your own histograms as a panel (F20 has none in this version). See research, section 4.

### What a buyer will ask

| Question | Answer | From |
|---|---|---|
| Can I chart one endpoint's latency? | Yes, and latency is an estimate there, as on every chart | Step 5 |
| Can I chart my own business metric? | Yes for gauges and counters: one chosen series, or all combined, by service, over at most 7 days at first (FR17). **Not your own histograms in this version,** so a custom duration's p95 stays on the metrics screen | Step 8 |
| Can I split by region or status? | No, except by choosing one series per panel (D16), so each region takes one of the 30 panels | Later, if asked |
| Logs on a dashboard? | No. Error groups and notes only | Not planned |
| Two projects side by side? | No. One project at a time, in the header | Not planned |
| Compare to last week? | Against the previous window only. **A chart cannot yet show this service's normal.** A later panel could read it from `detection_readings` (D54), if buyers ask | Later, if asked |
| What if someone wrecks the dashboard? | Restore a version, or a deleted dashboard for 30 days | Step 7 |
| Who else can edit? | Any member. No per-dashboard access | Not planned |
| Terraform or dashboards as code? | No. A person saves and loads a file | Not planned |
| What happens when the whole team opens it in an incident? | **Not answerable until T5.7.** NFR12 is the target | Step 5 |
| How many people and screens at once? | **Not answerable until T5.7.** By arithmetic, 15 worst-case screens at one API process and half that at two, with nothing shared. A second cap: 40 scans a page, so 30 compared numbers do not fit | Step 5 |
| Can another customer slow my dashboard? | **Not answerable until T5.7's two-tenant run.** A share per organization is designed. Self-hosted, with one organization, is unaffected | Step 5 |
| I pay for 90 days of traces and 395 of metrics. How far back does a dashboard go? | Rollup panels: what the plan keeps. Raw panels: 7 or 30 days at first, whichever is lower than the plan, until T5.7 measures more | Step 5 |
| We already have Grafana dashboards | No import. Recreate the few you actually use from templates (*how long is a guess until tried*); nothing stops you keeping Grafana for the rest. T2.0 states what share of ten real boards maps to a panel kind | Before Step 2 |
| Can I get all my dashboards out? | One at a time, as a file, in this version. No bulk export and no token (D9, D39). Self-hosted customers hold their own database | Not planned, unless the buyer conversations turn on it |
| Drag? | Do not promise it until T3.7 records the trial | Step 3 |

**A demo before Step 8 cannot honestly answer "yes" on custom metrics.** The first demo as "has dashboards" waits for Steps 1 to 9 and the demo project with real findings and deploys (`plan.md`, Step 11, T11.4).

## Decisions taken

*Recommended by the authors, founder may overrule.*

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| D1 | Panel data source | Fixed kinds / query builder / free-form | **Fixed kinds** (research R1). Foundation's rule, and the only form the tenant structure test passes. Cost: rivals answer more questions |
| D2 | Batch counting | One request / each scan charged | **Each scan charged, after merging** (R2). Otherwise the limits remain in name only |
| D3 | Batch shape | POST with panels / GET by id, POST for drafts | **`GET /dashboard-panels/read`, `POST /dashboard-panels/preview`** (R3, renamed by D41). Support view refuses a POST (`auth/dependencies.py:145`) |
| D4 | Storage | Child table / checked JSON | **Checked JSON with `schema_version`** (R5). Saves atomically, exports directly. A validator must do what Postgres cannot |
| D5 | Owner | Project / organization | **Organization, shown against a named project** (R6). Avoids a copy per environment. Costs a `not_in_project` state |
| D6 | Layout | Grid library / own grid with buttons / buttons then drag | **Buttons first, on an ordered list with a width of 12; drag is planned, not promised.** A one-day trial on mixed-width panels in Step 3 (T3.7) decides whether it ships in Step 10 or is dropped. "Every rival has drag" was wider than research read, which says the main rivals read do. No free placement; the cost is less freedom than Grafana. Sales must not mention drag until the trial is recorded |
| D7 | Variables | Full / one service picker / none | **One dashboard-wide service picker**. Differs from R10. "Start from this service" with no picker makes a copy per service, the very sprawl this feature exists to avoid, and the cost is small: a bound parameter. No other variable |
| D8 | Note panels | Markdown / plain text | **Plain text** (R8). No renderer to defend. A runbook link goes in the separate checked field (D37) |
| D9 | Token reach | Definitions and data / definitions / all-project tokens read definitions / none | **None.** Differs from R14. Dashboards are organization-wide and a token may be scoped to one project (`auth/dependencies.py:174`), so a one-project token reading definitions would learn another project's service names. An all-project token would not, and the customer reviewers asked for it twice. Left out because reading without writing is half of dashboards as code, and version history answers the fear of loss. Cost: no scripted backup, and a stated gap for buyers who keep dashboards in git. The buyer conversations test this |
| D10 | Public links | Include / leave out | **Leave out** (R9) |
| D11 | Refresh | Global setting / per dashboard | **Per dashboard: default 1 minute, floor 30 s** (R11; *guesses*). Rollups change by the minute (`rollup.py:42`) |
| D12 | Caps | None / protective | **30 panels, 40 scans a read refused at save, 64 KiB, 1,000 dashboards** (R12; *guesses*, set by FR33). Protection, not an upsell |
| D13 | Old windows | `too_old` from plan retention / read from the data | **"Data starts at", from the first row read, and "Your plan keeps 30 days" where a plan explains it.** Differs from research trap 2. A self-hosted install has no plan allowance (`deployment.py`), and a service started last week honestly reads the same way |
| D14 | Markers | Wait for F28 / a second read beside F28's / call F28's function | **Call F28's `markers_in_window` inside the context route, with its cap of 500, `truncated`, `basis` and `state`, and use `coverage_of`.** Changed from `deploys_in_window`, which is signed deploys only and would lose rollbacks and versions only seen in traffic, so one install would draw different markers on a dashboard and on the releases screen (F28 D29, D30, `design.md`, *Specification impact*). Coverage needs no ClickHouse there, so no separate read. Because a seen version can be planted, it is drawn and worded differently (FR18), and this is the correct end state over keeping signed deploys only |
| D15 | Overview | Rewrite as stored / keep, allow copy | **Keep** (R13) |
| D16 | Grouping | Any label / service plus fixed list | **Service only at first** (R15). Fixed labels when asked |
| D17 | Project mismatch | Keep reader's project / the address wins and is remembered / the address wins for this page only | **The address wins for this page and is not remembered, with a line saying so.** A remembered link would pin the whole console to staging, or to a past hour, and the reader would have to notice. Costs a small override in `selection.ts` |
| D18 | Concurrent editing | Last save wins / refuse the second / live co-editing | **Refuse the second, keep its draft.** Small, and loses nothing |
| D19 | History | Versions table / soft delete / both / none | **Both: versions and a 30-day restore** (*guesses*). Three reviewers called this the first objection, and BR7's "any member may edit" is safe only with a way back. A version is trimmed only past the newest 20 **and** older than 30 days, with a ceiling of 200, so a compromised account cannot flush the way back in seconds. Costs one child table, a partial unique index and one line in the expiry sweep |
| D20 | Duplicate titles | Allow / refuse | **Refuse, ignoring case.** Fifty similar names is the failure being avoided, and it powers FR41 |
| D21 | Last opened | Stamp on opening / on batch read / separate call | **On batch read, at most hourly, never in a support view.** A wall display counts, and it adds no route. Labelled "last opened", claiming no more than that |
| D22 | Import path | New route / browser opens a draft | **Browser reads the file, a validate-only route checks it, and then a draft opens** (D53). One validator, one save route |
| D23 | Metric series order | First / last of the panels | **Last of the panels, Step 8, ahead of the templates (Step 9).** Its reads are the hardest to get right, and no other panel depends on it. A buyer's first rebuild is often a business metric, so it must not be the very last thing built. The plan was renumbered to match this |
| D24 | Where Postgres facts come from | Inside the batch / lazy ClickHouse provider / a separate Postgres-only route | **A separate `GET /dashboards/{id}/context`.** `TenantRepository` answers 503 before the handler runs when the API has never connected (`deps.py:83-93`). A lazy provider would add a second path around a construction site a test holds to one. A separate route means notes, findings and deploys draw early and through any ClickHouse outage, which is what the on-call person needs |
| D25 | Time budget | 25 s deadline as written / 15 s plus three added constants / a deadline counted from arrival | **A 20 s deadline counted from the request's arrival, plus 5 s margin: `timeoutMs` 25 s** (*guesses*). Three added constants were wrong: a signed-in read waits up to 5 s in each of two buckets (`middleware/ratelimit.py:96,195-205`), and Postgres's pool wait of 10 s was unbudgeted (`settings.py:201`). The middleware records arrival in `scope["state"]`, so every wait shortens scan time rather than overrunning. Under the 30 s statement limit, each statement also runs with a lower fixed limit (D42) |
| D26 | Protecting the pool | Per-batch cap only / one cap per process / that plus a share per organization | **Per process, with a share per organization: 5 scans, 2 raw; 3 and 1 per organization where more than one can exist** (*guesses*). Two wall displays reached all 8 connections, about 5 reads at 1 GiB fill ClickHouse's roughly 4.8 GB cap (`sizing.md`; arithmetic), where inserts fail too, and an organization's in-flight limit of 32 never binds. Sales's "only when others are waiting" is a stronger use of capacity, and rejected for now: a running scan cannot be taken back, so the other tenant would still be refused |
| D27 | Many viewers | Each pays / raise the quota / short shared read in the process | **Short shared read, 30 s** (*guess*), with no new service. 30 people at 30 scans a minute is 900, over the organization's 600, so nothing else meets NFR12. Raising the quota removes the protection. Keyed by tenant, project, reader, arguments and window. The limiter already keeps state in process memory |
| D28 | References to rows | Ids / ids stripped on export / names | **Names, resolved in the displayed project at read time.** The security reviewer recommended keeping ids. Ids would make a dashboard read `not_in_project` on staging, defeating D5, and would break AC40. Check names are not unique, so two matches read `ambiguous`. A rename breaks the panel, visibly |
| D29 | Old line after a failure | Keep it dimmed / remove it at once / keep it briefly under a strip | **Live window: one interval under a filled strip, then remove it, including in a page-wide failure. Fixed window: keep it with no limit.** Dimmed alone is the healthy flat line Goal 4 forbids, removing at once blanks a page for one blip, and numbers for a closed period are not old |
| D30 | A refused panel's retry | Next refresh / scheduled catch-up / button only | **Scheduled catch-up of only the refused panels, up to 3 times, plus "Read again".** UX preferred a button on a fixed window; the customer wanted it to fill by itself. It fills by itself, because that read never finished. Paused waits, since the person asked to stop |
| D31 | Over the scan cap | Refuse on read / refuse on save | **Refuse on save, and `over_limit` for stored ones.** A saved dashboard that fails forever is the worst outcome. `dashboardpanels` imports the compiler from `dashboards`, never the reverse |
| D32 | More kinds | Leave out / add | **Add `statement_trend`, `endpoint_trend` and `top_nodes_trend`.** The Problem's own example needs the first. On-call needs the second. A template cannot name a customer's nodes, so *Infrastructure* cannot exist without the third |
| D33 | Own metrics | Service only / one chosen series | **A chosen `series_hash`, bound as a parameter,** from a listed set of attribute combinations. No runtime SQL. This is less than group by label, D16 stands |
| D34 | First read | One batch / first screenful then the rest | **One batch, with early-drawn Postgres parts and "Still reading".** UX preferred two batches. Two would run shared scans twice and cost quota. A drag is different, since no window is shared: it reads only visible panels (FR53). If T5.7 shows a slow scan holding the top of the page past its pass mark, split using the `panels` parameter, which exists anyway |
| D35 | Wall display | Remove the use case / simple full-screen view | **Simple full-screen view, and docs for a dedicated viewer account.** Small, and the use case is named in the Problem |
| D36 | Tuning self-hosted | Code constants / settings | **Settings for panel cap, scan cap, refresh floor, raw windows and scans at once, with today's values as defaults** (as `clickhouse_pool_size` is), validated at start so scans times refreshes a minute still fit the quota share. The telemetry quota itself stays product policy (`policy.py:14`). A hosted install keeps defaults. Self-hosted opens first (`vision.md:234`) |
| D37 | Runbook links | None / rendered in notes / a separate checked field | **A separate field, `https` only, up to 5, no user name or password, no credential-shaped query key, host shown.** Notes stay plain text. The console draws the link |
| D38 | No-AI test | Exempt `mcp` through `lifespan` / remove the import | **Remove the import.** `lifespan.py:21` imports the SDK only for one type (`:137`). A small Protocol fixes the cause, and no exemption is needed. F19's D55 and F5's T1.5 need the same fix: whichever lands first does it. The developer reviewer offered the exemption; the security reviewer's root-cause fix wins |
| D39 | Bulk export | Export all / leave out | **Leave out.** Versions and restore answer lost work, and a bulk file has no import. Stated to buyers. A customer reviewer asked for it, so the buyer conversations test it (founder decision 1). If they turn on it, it is a server route, since 1,000 reads from a browser would spend the default tier's 300 a minute |
| D40 | Step order | Plan's order / spec's order | **Spec's: 8 metric series, 9 templates.** D23's reason holds, and the founder's fallback then reads correctly |
| D41 | Batch route's name | `/dashboards/panels:read` / `/dashboard-panels/read` | **`/dashboard-panels`.** A colon path made router order load-bearing, the first in the repository, with a 422 on every read if wrong. The foundation permits a rename if the reason is given (section 8), and this removes a trap and its test |
| D42 | Shared read's rules | As first written / explicit rules | **Only a charged, capped leader creates an in-flight entry, so a refusal is never shared. A joiner whose leader failed runs its own attempt if at least 1 s remains. The task's statement limit is a fixed 15 s (*guess*), not tied to one requester, and it holds its process-cap place until the statement ends. A task with no reader left is cancelled. Bounded by 100,000 rows retained, 25,000 for one organization and 2,000 per answer (*guesses*)** |
| D43 | Slots for later scans | Each later scan takes slots / quota only | **Quota only.** At one process the batch's permits and the process cap both bind before the person's cap of 8, and the organization's 32 never binds. Slots cost the rider rule, moving `_wait_for_slot`, a second refusal path and a third wait. BR6 holds, since quota is what charges a scan. Brought back only if T5.7 finds what the process cap misses |
| D44 | Latency's promise | Digit equality / counts exact, latency estimated | **Counts exact, latency an estimate on every rollup panel, and said so.** All of them merge the same quantile state (`services/queries.py:47`), and ClickHouse calls the result non-deterministic. Labelling only endpoints was wrong, so that label goes |
| D45 | Second read account | Caps in the API only / a server-side bound | **Server-side: `nexovue_api_reader`, for all the API's reads.** Caps in the API cannot hold across processes or for other screens, and the API and scheduler share one account today (`nexovue.yaml:291,360`). The customer's lower `priority` is not adopted yet: `priority` needs the other queries to carry it too, and pausing was not tested. T5.7 records whether it is needed |
| D46 | Which picker control | `<datalist>` / a custom listbox / select plus typed field | **A native select with escaped labels and raw values, plus a separate typed field.** A datalist's value is both what is shown and what is sent, by the HTML standard, so it cannot show `[U+202E]` and hold the raw name. Not yet tried on a browser, so T3.3 tries phone Safari and Chrome, as QA asked |
| D47 | Purge | None / any editor / owner and admin | **Owner and admin, by a new `PURGE_DASHBOARDS`.** Rotating a credential does not remove personal data, and a warning is not a remedy. Precedent: audit rewriting on member removal (`members/service.py:225-232`) |
| D48 | "All" on a metric | Up to 20 lines / one combined line | **One combined line, stating how many series.** 20 lines cannot be told apart with 6 colours and 5 dashes, and hiding series from a sum would make the sum false |
| D49 | Histograms | Add a panel now / state the gap | **State the gap.** T8.1 first settles the suspected subtraction fault in `HISTOGRAM_SQL`. A `histogram_quantile_trend` kind can follow, and adding it now would build on a read not yet proved |
| D50 | Long metric windows | Fixed hourly rollup now / measure first | **Measure first (FR17).** A rollup means a new writer path and unmeasured storage. It comes back if T5.7 shows single-service 30 and 90-day reads cannot hold, which would be a stated gap until then |
| D51 | Browser-only checks | Add an e2e job to CI / named hand-run points | **`just e2e` at each step's end and before release, with results in `status.md`.** The repository calls e2e a release-risk check, outside `just check` (`justfile:279-281`, `ci.yml:77-78`). Adding a job is its own decision, not F20's |
| D52 | Alert incidents | Nothing / strip and screen link / those plus shading and a dashboard field on rules | **Strip and screen link.** Shading needs a second colour rule and a read per panel, and a field on rules changes F21's schema, where "Dashboards showing this service" already reaches the board |
| D53 | Import check | Save only / a validate-only route | **`POST /dashboards/validate`, gated, running no scans.** The documents disagreed: the error table said nothing opens, FR42 said validate at save, and a hostile file could sit as a draft |
| D54 | Normal on a chart | Add `show_normal` now from `detection_baselines` / later from `detection_readings` | **Not in this version.** `detection_baselines` keeps one hour (`detection/baselines.py:117`), so it cannot serve a past window. The stored readings hold a normal, so a later panel can read them, at the cost of one scan per service. It would be a real point of difference, and the buyer conversations ask |
| D55 | Where delete, duplicate, versions and purge live on the fallback | Leave them on the edit screen / build the edit screen anyway / one component the read view mounts | **One component, `DashboardActions`.** The edit screen does not exist on that path, and one component means the rules are written once. Recommended by the authors, founder may overrule |
| D56 | Judging Steps 4, 5 and 7 on the fallback | Move the templates first / skip judging / seeded definitions | **Seeded definitions, for local judging only.** The templates need kinds from Step 5 and leave some out. Recommended by the authors, founder may overrule |
| D57 | Phone Safari in AC74 | Add a WebKit Playwright project / a recorded hand check | **A recorded hand check.** WebKit on a desktop is not phone Safari, and browser tests are outside CI anyway (D51). Recommended by the authors, founder may overrule |
| D58 | When `/` moves to the Findings feed | With the rest of F20, in Stage 6 / as soon as F6 merges, as F20's Step 1 alone | **As soon as F6 merges, as plan Step 1 alone, ahead of Stages 2 to 5.** Decided 2026-11-02; was founder decision 2. It is not a new choice, only the timing of one already made: FD15 and the catalog say the feed is the front door (`feature-catalog.md:317-325`), and the code itself names the trigger, "Overview holds `/` until the insight engine exists" (`apps/web/src/app/router.tsx:236`). F6 is that engine. Step 1 has no dashboard code and no dependency on F28 or the builder (`plan.md`, Step 1). nexovue is pre-deployment, so no user is surprised, and it is one route to move back if it is wrong. Its one real risk, a landing page with nothing to show during F6's 3 days of learning (`anomaly-detection/spec.md:216-220`), is handled by T1.7's charts and learning line. The founder still approves it at the spec gate, and may approve the front-door parts (*The front door*, design section 1, plan Step 1) ahead of the rest. No web research was needed: the answer rests on the code and recorded decisions |
| D59 | What "a cap too low to be useful" means after T5.7 | Leave it to judgement / a stated test | **A stated test: every template in *The templates* shows no `refused` panel for one person (AC129), and NFR12's two cases pass.** Decided 2026-11-02. If the measured caps pass both, they are set from the report with no further question. If a template fails, its raw scans fall, as *The templates* already says. **Only if NFR12 cannot pass at any cap** does it go to the founder, because then "a team can watch one dashboard in an incident" cannot be said to buyers. The plan said "too low to be useful" without saying how to tell |
| D60 | Step 2's precondition on buyer conversations | F20 holds its own round / F20's questions join the one round | **F20's questions join the one round the foundation approved (its founder decision 8; not table row FD8, which is who may ask the AI), held before F28's Step 1b.** Settled by the foundation, 2026-11-02, not re-asked. F20 is Stage 6, after F28, so the round has always happened before Step 2. Step 2's precondition becomes: the round's notes are recorded, and founder decision 1's rule has been applied to them, with T2.0's mapping in hand |
| D61 | Do read limits rise with the plan? | Equal on every plan / higher on bigger plans | **Equal on every plan, and `multiplier_for` stays at 1.0 deliberately (`apps/api/src/nexovue_api/ratelimit/policy.py:303-311`), so sales does not imply otherwise.** Decided 2026-11-02 in review; was founder decision 3. It is not a new pricing choice: the foundation's FD20 (no feature gated by plan) and section 10 ("limits here are for protecting the system, stated as such, and not as upsells"; plans differ by service count, bytes and retention) already answer it, and BR8 repeats it. Raising limits by plan would be the new decision, and that one is the founder's whenever plans are priced. The code leaves the door open (`policy.py:15`), so nothing is lost. Rivals checked by search on 2026-11-02 publish no per-plan dashboard query limits (sources under *Founder decisions needed*) |

## Founder decisions needed

**Settled by the foundation, not asked again.** Whether to speak to five to eight buyers at all is the foundation's founder decision 8, approved 2026-11-02: one round, before F28's Step 1b, carrying F20's questions below (`phase-2-foundation.md`, *Founder decisions*, 8). Step 2 waits for it (D60). What stays yours is decision 1: what the answers change.

**1. Approved 2026-11-02, as recommended (foundation item 21): the rule below is fixed before the round.** **What the buyer answers change: the builder, or templates only.** The evidence for a builder is weak, and F20 is the largest thing in Stage 6. In the round the foundation's founder decision 8 approved, ask about what they have done, not what they would reject: "Show me the last dashboard you built. Who looks at it? What do you use today, and what would make you move it?" "At your last incident, how did you find out whether a chart's change lined up with a known problem or deploy? Show me." Record that answer against the sales line. Then: "Would you reject us for no Terraform or API management of dashboards, no bulk export, no variables beyond a service picker, no public links, or no histograms on a dashboard?" "What would you do with your existing dashboards?", with T2.0's mapping in hand. The questions are the authors' and may change; the rule below is yours.

**Recommendation:** set the rule before the first call. If at least half of those asked (*a guess*: 3 of 5, 4 of 8) say they would not buy without building or editing their own panels, build the full plan. Count only answers backed by a dashboard they actually built and use, from the first question. Otherwise, ship **templates only: templates and the picker, with no builder, no metric series and no drag.** By number, that is plan Steps 1, 2, 4 to 7 and 9, without the builder (Step 3), metric series (Step 8) or drag (Step 10). Teams then get fixed templates and the picker, save with a title (T2.18's small dialog), and cannot edit a panel. Delete, duplicate, versions and purge move from the edit screen to the read view (plan, TF.1), and the founder judges Steps 4, 5 and 7 on seeded definitions (TF.2). Import and restore save directly in that case.

**Why it is yours:** it decides whether customers are sold a dashboard builder or a set of fixed dashboards, which is scope that changes what is sold. **Blocks:** plan Step 2. Set the threshold before the round, not after reading the answers.

**2. Answered 2026-11-02 by the authors: see D58.** `/` moves to the Findings feed as soon as F6 merges. It is timing of a decision you already made (FD15), and the code names the trigger (`router.tsx:236`).

**3. Decided 2026-11-02 in review: see D61.** Read limits are equal on every plan. The foundation's FD20 and section 10 already say limits protect the system and are not sold; only a future choice to sell higher limits would come back to you.

**4. Approved 2026-11-02, as recommended (foundation item 14): all three sentences.** Caps and "how many at once" are published only after T5.7 is rerun on Oracle after deployment, since no test server is rented (item 9). **Approve the public wording for dashboards.** Three sentences, under *What this brings, stated honestly*: the positioning line, "Findings tell you what broke. Dashboards hold the few numbers your team chose to watch, and start from a template, never a blank page."; the sales line that goes into foundation section 12's future-tense list, labelled unproven by buyers; and the reassurance, "Dashboards cost nothing extra, on any plan, and we never price by person." The reassurance is a public promise about price, so it needs your approval too; it matches FD20 and `vision.md:152`. If decision 1 ends in templates only, the positioning line still holds, and nothing may say "build your own dashboards". **Recommendation:** approve all three as written, with the *Must not be claimed* list kept beside them, and the caps and "how many at once" stated only after T5.7. **Why it is yours:** it is a public claim to customers. **Blocks:** T11.5's public docs only; nothing is built on it.

*Sources for D61 (was decision 3), read 2026-11-02:* [Grafana Cloud Loki limits](https://grafana.com/docs/grafana-cloud/observe-and-act/send-data/logs/loki-limits/), [Grafana community, rate limits on free tier](https://community.grafana.com/t/grafana-cloud-loki-ingestion-rate-limit-increasement/104298), [SigNoz ingestion limits](https://signoz.io/docs/cost-control/ingestion-limits), [SigNoz query performance](https://signoz.io/docs/querying/query-performance/).

## Open decisions

None. Founder decisions 1 and 4 were approved on 2026-11-02 (3 is decided as D61). Everything else is decided under *Decisions taken*, and may be overruled. The test server (`phase-2-plan.md`, open question 2) was answered on 2026-11-02: none rented, so T5.7 runs on the Mac as a build gate and is rerun on Oracle before any cap is published.
