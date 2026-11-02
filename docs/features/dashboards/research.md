# F20 Dashboards: research

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed, 2026-11-02. Input to F20's specification and design. It approves nothing, and no code is
> written from it. Stage 6 of [`phase-2-plan.md`](../../product/phase-2-plan.md), built after the chain.
>
> **How it was checked.** The code was read on branch `feature/anomaly-detection`, with uncommitted work present.
> Line numbers are for that working tree and will drift. Nothing was run: no query, no load test, no browser. Every
> number marked *guess* or *arithmetic* is not a measurement. Competitors' pages were read on 2026-11-02 through a
> summarizing tool, so check a page before quoting it in public.
>
> **Reviewed, 2026-11-02.** The original text stays as a record. Corrections are marked *Corrected in review*. Facts added then are in section 4, *Added in review*, each with its source and read date.

## The short version

1. **The evidence says a builder is expected, not a reason to buy.** It rests on peers having one, and on one
   unverifiable page ([scope research](../../research/phase-2-scope-2026-10-30.md)). The complaints we found are about
   upkeep and sprawl (too many dashboards, nobody sure which is right), not missing features. So F20 aims for
   parity on a narrow set, and spends its care on keeping dashboards few and trustworthy.
2. **The hard part is not the drag-and-drop. It is reading the data safely.** Three things in today's code would make
   a simple builder wrong: 30 separate reads spend the whole rate limit; a batch route would quietly walk around that
   limit; and there is no general way to read a metric over time, where the obvious SQL returns wrong numbers.
3. **Keep the fixed list of panel types and fixed SQL.** No query language, no free-form SQL. That is the repository's
   tenant-safety rule, and it also fits the size of the team. It costs flexibility, and Grafana stays ahead.
4. **The front door moves to the Findings feed, and it should not wait for the builder.** It is a small change sitting
   behind the largest wait in the plan.
5. **No public links in the first version.** ~~They are the feature with the worst security record among the products
   read~~, and nobody has asked for them. *Corrected in review:* public links add an unauthenticated way to reach the query path, and Grafana published CVE-2023-2801 against them. **No comparison of security records was made,** so this must not reach sales material.

## Words used here

| Term | Meaning |
|---|---|
| **Panel** | One chart, number or table on a dashboard |
| **Builder** | The screen where a person chooses, places and saves panels |
| **Composed dashboard** | One nexovue arranges by itself, like today's Overview |
| **RED / USE** | Two standard ways to choose what to chart. Rate, errors, duration for services; utilization, saturation, errors for machines |
| **Scan** | One read of the columnar telemetry database (ClickHouse). Far more costly than a Postgres row read (`ratelimit/policy.py:8-11`) |
| **Fan-out** | How many reads one screen starts at once |
| **Rollup** | A table of per-minute totals written ahead of time, so a chart need not read every span |
| **Cumulative counter** | A metric that reports a running total since a process started. The useful number is how much it rose, not its value |
| **Series** | One line on a chart: a metric plus one set of labels, such as one service instance |
| **Sprawl** | Dashboards multiplying until nobody knows which to trust |
| **IDOR** | "Insecure direct object reference": getting another customer's record by guessing its id |

## 1. What exists

### Exists and can be reused

| What | Where | Note for F20 |
|---|---|---|
| Composed Overview on `/` | `router.tsx:245`, `features/overview/OverviewScreen.tsx:40` | Three RED panels, worst services, one line per surface |
| Project RED, merged by the store | `services/queries.py:238,247`, `services/router.py:109` | One statement gives rate, errors and all three percentiles, so three panels can share one scan |
| One service's trend | `services/queries.py:147`, `services/router.py:82` | With no service named, it returns **every** service and has no `LIMIT`. Not usable as it stands for "top 5 lines" |
| Ranked services and endpoints | `services/queries.py:103,121` | Ordering is done in the store, which is correct; `LIMIT` is bound |
| Exact per-service counts and histogram | `service_stats_1m`, `000035` | Kept 35 days. `endpoint_stats_1m` keeps quantile state, so endpoints are approximate ([code map](../../research/phase-2-code-map-2026-11.md)) |
| One point-count rule | `rollup.py:38,45` | About 60 points per chart, at a one-minute floor |
| Other read routes a panel could use | `errorgroups/router.py:77`, `infrastructure/router.py:94`, `synthetics/router.py:173`, `rum/router.py:45`, `dbstats/router.py:87`, `findings/router.py:63`, `servicemap/router.py:27` | Each is its own shape and its own scan |
| One histogram; one duration heatmap | `metrics/router.py:71,92` | These are the only metric value reads |
| Chart | `shared/components/TrendChart.tsx:88-118` | uPlot, with deploy rules, shaded spans and bands. See the gaps below |
| Others | `HistogramChart`, `DurationHeatmap`, `DataTable`, `Sparkline`, `Delta`, `UnsavedChanges`, `Permitted`, `EmptyState`, `InlineFailure` | A saved layout can use the form guard as is (`UnsavedChanges.tsx`) |
| Time window, refresh, pause | `shared/selection.ts:338-369`, `shared/refresh.ts:34-40,124` | Refresh stops itself on a closed time window |
| Edit permission (proposed) | `EDIT_DASHBOARDS`, [foundation](../../product/phase-2-foundation.md) section 5 | Owner, admin, member. A viewer reads. `auth/permissions.py:16-42` must gain it |
| Names (proposed) | `dashboards` table; `/dashboards`, `/dashboards/panels:read`; console `/dashboards` | Foundation section 8 |

### Does not exist

Any stored dashboard, a builder screen, layout, sharing, per-dashboard links, a general metric read, a drag or grid
library (`apps/web/package.json:18-32`), and any record of when something was last viewed.

### Decisions already made

- Insights and dashboards are both first-class; the composed Overview is not a lesser mode (`vision.md:236`).
- An auto-composed dashboard is "a fixed instance of what a builder would produce" (`feature-catalog.md:321-323`).
- The Findings feed is what a person lands on (`feature-catalog.md:325`). Foundation FD15 gives F20 the move.
- Fixed panel types compiled to fixed statements; no AI writes a dashboard; no small count limit as an upsell; parity
  with Grafana is the aim (foundation sections 9, 10, 12).

## 2. What the code says that a simple design would get wrong

### Trap 1: the batch route walks around the rate limit

The foundation asks for a batch read, because 30 panels at a 10-second refresh spend 180 reads a minute, the whole
personal quota (`policy.py:78`; arithmetic). That is right, and it creates a new hole.

The limiter counts **HTTP requests**. One request holds one slot, however much it does (`middleware/ratelimit.py:193-213`).
A batch of 30 panels would count as 1 against the quota and 1 against the cap of 8 scans in flight (`policy.py:92`).
Those limits exist because "ten concurrent ones will hurt the cluster" (`policy.py:8-11`). A person holding four batch
requests could run 120 scans at once, while every counter reads as normal.

**What must hold:** batching must reduce scans, not hide them.

- **Merge first.** Panels asking for the same statement and window become one scan. Three RED panels on one service
  are one read, because `TREND_SQL` already returns every number they need. This is the real saving.
- **Charge what remains.** Each distinct scan in a batch must take a real slot and count against the quota, through the
  limiter's own `acquire_slot` (`ratelimit.py:98`). Whether a partly refused batch returns partial results is a design
  question, and partial results are the recommendation (next trap).
- **Update the fan-out test's meaning.** `LARGEST_SCREEN_FANOUT` is "measured on the service screen"
  (`test_ratelimit_coverage.py:110`). A dashboard is not a fixed screen, so its limit must come from a dashboard's
  panel cap, not this constant.

### Trap 2: a batch must not fail as one

Today each panel fails alone, and a screen says plainly what it could not read (`refresh.ts:14-18`). A batch has one
HTTP status. So every panel in the answer needs its own status: `ok`, `empty`, `too_old`, `refused`, `failed`. A panel
that could not be read must never draw as a flat line at zero, because on a dashboard that reads as an outage
(`services/service.py:137-138`).

### Trap 3: a POST batch breaks under support view, and at the door

- Support view refuses anything but GET, HEAD and OPTIONS (`auth/dependencies.py:145`). A batch sent as POST would
  leave a customer's dashboard blank while nexovue support is helping them.
- Every POST must be listed as gated or as exempt with a reason (`test_authorization_coverage.py:331`).
  `symbolicate` is the precedent for a POST that reads (`:139-142`).

**The way out:** a saved dashboard's panels are already in Postgres, so the request does not need to carry them.
`GET /dashboards/panels:read?dashboard_id=…&project_id=…&from=…&to=…` carries only ids and a window. An unsaved draft in
the editor does need a body, so **draft preview** is a separate POST gated by `EDIT_DASHBOARDS`, which a support view
has no reason to reach. This refines the foundation's route name without changing it, and F20's design must say so.

### Trap 4: which rate-limit tier a route lands in depends on its prefix

`tier_for_path` matches by prefix (`policy.py:286`). Adding `/dashboards` to `TELEMETRY_PATHS` would put every list and
save on the costly tier as well. Adding only `/dashboards/panels` keeps those on the default tier and still protects the
reads, provided the reads live on their own router with that prefix, which is what
`test_ratelimit_coverage.py:73,83` compares against. The GET shape above fits this; a path like
`/dashboards/{id}/panels` would not.

### Trap 5: a general metric read is easy to write and wrong

The metrics router has no series read for a sum or gauge; only histograms are readable (`metrics/queries.py:48`). The
queries nearby record how many ways this goes wrong:

| Wrong result | Why | Where the code says it |
|---|---|---|
| Traffic 360 times too high | Cumulative points repeat the running total; summing them adds the total to itself | `metrics/queries.py:175-181` |
| A large false spike | A restart resets the counter | `metrics/queries.py:183-188`, `infrastructure/service.py:578-590` |
| Inflated counts | A metric row is not unique, because a redelivered batch is kept | `metrics/queries.py:90-93` |
| Milliseconds added to seconds | Two bucket layouts under one name | `metrics/queries.py:190-194` |
| One name, two shapes | Nothing stops a gauge on Monday and a sum on Tuesday | `metrics/queries.py:17-20` |

**A new finding, which needs checking.** `series_hash` is computed from `attrs` alone (`000003_metrics_sum.up.sql:27`), and
`resource_attrs` is outside it. Service name and instance live outside the hash too. Two instances of one service that
report the same `attrs` therefore share a `series_hash`. Treating that hash as one series means subtracting one
instance's total from another's, which gives a wrong rate. The infrastructure read avoids this by naming the host,
container and instance from `resource_attrs` (`infrastructure/queries.py:67-71`). `HISTOGRAM_SQL` groups by `series_hash`
and bounds with `any(service_name)` (`metrics/queries.py:225-227,262`), so it may carry this problem today. **This comes
from reading the code, and no query was run.** F20's design must test it; if real, it is a separate bug report, not F20's
to absorb quietly.

The general metric read needs its own design, its own live test against the demo's cumulative metrics, and a series
cap that says how many it hid.

### Trap 6: grouping by any label cannot use a bound parameter

Label paths in these statements are written into the SQL as constants (`dbstats/queries.py:34-40`), and a test refuses SQL
built from a runtime value (`test_tenant_chokepoint_structure.py:325`). So "group by the label you choose" cannot use that
form. The bindable alternative reads through `toJSONString(attrs)`, which the code measured at 2.29 s against 0.09 s over
six hours (`metrics/queries.py:97-99`). First version: group by service only, plus a short fixed list of labels as
constants. Anything wider waits for a real request and a measurement.

### Trap 7: the chart cannot yet do a dashboard's job

| Gap in `TrendChart.tsx` | Line | Why it matters |
|---|---|---|
| Fixed height of 132 px | `:21,188,372` | A resizable panel cannot grow. Only width follows its box |
| The y axis always starts at 0 | `:395` | Correct for rates. Wrong for a gauge that goes negative, or one that moves slightly around 800 |
| All lines one colour, three dash styles, then repeats | `:41,426-432` | Right for p50/p95/p99. A "top 5 services" chart would be lines nobody can tell apart |
| No value under the cursor | `:377,385` | `live: false`, cursor points off, and no legend on a single line. An on-call person cannot read a number at a moment |
| Canvas with one sentence for screen readers | `:214` | The numbers need a table view for accessibility |

These need changes to a chart used across the console, so each change must keep existing screens as they are. Linking
the cursor across panels is a uPlot capability (its documentation mentions synchronization, not read in detail), and it
would make a dashboard more useful during an incident than almost anything else on this list.

### Trap 8: a link cannot say what it shows

The project and time window are kept only in the browser's local storage (`selection.ts:65,73,112`). Paste a dashboard
link into an incident channel and a colleague opens it with **their own** project and window. A colleague with staging
selected reads staging numbers under a production title. F20 must put project and window in the dashboard's address,
and must show which project is displayed.

### Trap 9: old time ranges

Seeded Free retention is 7 days raw and 30 days rollups (`0005_plans_table.py:34-39`); these values were not traced
through later migrations. `service_stats_1m` lasts 35 days. A saved "last 90 days" panel on Free would show part of a line
that looks like a quiet start. The panel must say how far its data goes back. *Corrected in review:* panels do not read `service_stats_1m`, which only detection reads (`000035_service_stats_1m.up.sql:31-32`). They read `endpoint_stats_1m`, which keeps the plan's derived retention (`000008_endpoint_stats_1m.up.sql:27`), so the 35 days does not reach a panel.

### Smaller things

- The comments at `overview/api.ts:143-151` and `OverviewScreen.tsx:48-51` say a fifth read is refused. The cap is 8 and
  a read waits (`policy.py:92`). They are stale, and the staggered reads may be needless.
- Deploy markers read only the newest 50 deploys (`overview/api.ts:219`, code map). F28's windowed read fixes this; a
  long-window dashboard must wait for it, or say so.
- A new table must join `organizations/tables.py`, or `test_deletion_live.py` fails.
- `/` and the sidebar are held together by `route-manifest.test.ts`, and `NavPath` is a closed list
  (`route-manifest.ts:18-34`).
- `GET` routes using `CurrentReader` publish themselves on the customer's read API automatically (`readsurface/spec.py:75`).
  That choice is made by which dependency a route names, so it must be made on purpose.

## 3. What users need

**Evidence, honestly graded.** No buyer conversation has happened. The surveys found are seller-published. The founder is
already advised to speak to five to eight people before F5 and F20 are built (foundation, *Founder decisions*). Everything
below is reasoning from the code and public writing, not a finding from customers.

| Person | Wants | What this means for F20 | What would drive them away |
|---|---|---|---|
| **Buyer** (CTO, small team) | To tick "has dashboards", and to know the tool won't become a maintenance chore | Unlimited dashboards at no per-person cost. A demo that starts from something already useful | A blank page; a feature held back to an upsell |
| **On-call engineer** at 3 a.m. | "Is it my service? Did a deploy do it? What is the number at 02:14?" | Deploy rules, finding spans, cursor value, shared cursor, click-through to the service, pause | A chart that looks fine because a read failed; a stale wall screen with no warning |
| **New developer** | To find the one right dashboard | Owner and last-viewed shown; "start from this service"; a template rather than a copy | Fifty similar names, as in Grafana's "copy and change one thing" warning |
| **Team lead with a wall display** | A screen that stays right all day | A dashboard-level refresh setting, a visible "last updated", full-width layout | One screen eating the team's read allowance |
| **Admin** | To control who edits, and to clean up | `EDIT_DASHBOARDS`, an audit entry per change, deletion by anyone with the permission, a list sorted by last viewed | Orphaned dashboards after someone leaves |
| **Viewer** | To read and change window, without being able to break anything | Read-only by the same rule as every screen | Edit buttons that fail when clicked (`Permitted.tsx`) |
| **Attacker** | See section 6 | | |

The customers research adds two requirements: every dashboard should show when it was last viewed and should start from a
service, so it does not compete with the findings-first home ([customers](../../research/phase-2-customers-2026-11.md)).
The founder's standing rule also applies: screens use the window, with no capped containers.

## 4. How competitors do it, and at what price

Read from their own pages on 2026-11-02. "Not stated" means the page read did not say.

| Product | Panels and queries | Layout | Variables | Reuse and code | Sharing | Control | Price for dashboards |
|---|---|---|---|---|---|---|---|
| **Grafana** | Up to 26 queries a panel | 24 columns | Yes | Versioned JSON; scripted generation recommended | Public link, read-only, stored queries only; variables unsupported. Named invitees are Enterprise/Cloud preview | Usage insights and query caching are Enterprise/Cloud | Open source free when self-hosted. Cloud Free: 3 active users, 14 days. Pro: from $19 a month plus from $8 per active user, 3 included. Enterprise from $25,000 a year |
| **SigNoz** | Query builder across signals; at most 10 queries and functions per panel | 12 columns; 500 sections, 100 items each | Yes; still usable on locked ones | JSON import, **including Grafana exports**; 124 templates by its index, community-kept | Public sharing; variables do not work there; optional time range. *Corrected in review:* not in Community Edition ("available on the Self-Hosted Enterprise plan and SigNoz Cloud", its page, read 2026-11-02) | Locking in all editions; per-dashboard access | "Unlimited dashboards & alerts" on all editions; "No user-based pricing". Teams from $49 a month with $49 of usage |
| **Datadog** | Many widget types, including SLO | 12-wide grid; older free-form type | Yes | Not read | Public link, refreshing every 30 s whatever the window; embeds; scheduled email reports | Org page listing public shares | No dashboard limit listed. Free: 5 hosts, 1-day metrics. Pro from $15 per host a month annually |
| **Sentry** | Line, area, bar, table, big number, markdown | Grid | Page filters: project, environment, date, release, custom | Duplicating; widget library; AI generation in open beta | Shared across the organization | Edit Access by team; creator and owners always keep it | Plan limits not stated on the pages read |
| **OpenObserve** | 20 types, including custom charts and **HTML** | Tabs | Yes | JSON import | Public dashboards exist; not read | Not read | AGPL self-hosted edition; dashboards by edition not read |
| **Uptrace** | Own Prometheus-style language | Grid plus table type linking to filtered grids | Not read | YAML; prebuilt, and created automatically when matching metrics arrive | Not read | Not read | Not read |
| **Honeycomb** | Boards of saved queries | Not read | Not read | Duplicating across environments | Shareable links | Not read | Board limits not listed. Pro from $150 a month, SLOs limited to 2 |
| **Perses** (not a vendor product) | Prometheus, Tempo, Loki, Pyroscope; plugins | Not read | Not read | Go and CUE SDKs; an open dashboard specification; CNCF sandbox; Apache 2.0 | Not read | Not read | Free |

**AI building dashboards.** Datadog, SigNoz's Noz, OpenObserve, Sentry and Grafana Assistant all offer it (competitors
research; Sentry's page above). Nexovue's foundation rules it out, because AI that writes is not read-only. This is a real
gap that a buyer may notice. It is defensible, since a model's output could be turned into a safe, checked definition one
day, but that is a later decision.

**Added in review,** read 2026-11-02 through the same summarizing tool. Check before public use (plan T11.5).

| Fact | Source |
|---|---|
| **Netdata**, not in the earlier table. "Each chart in the Metrics tab also features an Anomaly Rate ribbon", purple, on each chart. Agent GPL v3+. UI closed-source, "free to use with Netdata Agent and Cloud". README says "Zero-touch ML". Whether ML is on by default was not stated outright. Custom dashboards in Netdata Cloud. Its anomalies are per metric, and are not lifecycle findings | [anomaly advisor](https://learn.netdata.cloud/docs/ml-and-troubleshooting/anomaly-advisor), [README](https://github.com/netdata/netdata) |
| **Datadog Change Overlays.** Puts deployments and feature flags on dashboard graphs from "Show Overlays". The "Most Relevant" service is the one most referenced in the widgets' queries. That page lists "Automatic Faulty Deployment Detection" as further reading and does not describe it, so a filter to faulty changes is **not confirmed here**. Timeseries widgets also take event overlays | [change overlays](https://docs.datadoghq.com/dashboards/change_overlays/), [timeseries](https://docs.datadoghq.com/dashboards/widgets/timeseries/) |
| **Datadog anomalies on graphs.** `anomalies()`: "Overlay a gray band on the metric showing the expected behavior" | [algorithms](https://docs.datadoghq.com/dashboards/functions/algorithms/) |
| **Datadog TV Mode** | [configure](https://docs.datadoghq.com/dashboards/configure/) |
| **Datadog dashboard usage.** `viewed_at`, and `filter[viewed_before]` "including dashboards that have never been viewed". Null without RUM, refreshed daily | [usage stats API](https://docs.datadoghq.com/api/latest/dashboards/get-usage-stats-for-all-dashboards/) |
| **Grafana kiosk mode.** Hides the main menu and top bar. Built-in annotations include "alert state history annotations" | [using dashboards](https://grafana.com/docs/grafana/latest/visualizations/dashboards/use-dashboards/), [annotations](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/annotate-visualizations/) |
| **SigNoz anomaly detection** is not in Community Edition (✗), and is in Teams and Enterprise. "No user-based pricing" | [pricing](https://signoz.io/pricing/) |
| **SigNoz migration.** Guides for Datadog, with a tool translating dashboard JSON, and New Relic, by hand | [Datadog](https://signoz.io/docs/migration/migrate-from-datadog/dashboards/), [New Relic](https://signoz.io/docs/migration/migrate-from-newrelic/dashboards/) |
| **SigNoz Terraform.** The page is tagged Cloud and Self-Host, and `signoz_dashboard` needs v0.135.0 or newer. It does not name Community Edition, so that remains **unconfirmed**, and the review's "v0.85.0" was not found there | [provider](https://signoz.io/docs/dashboards/terraform-provider-signoz/) |
| **Honeycomb.** "Unlimited seats" on its pricing page. Plans are by event volume | [pricing](https://www.honeycomb.io/pricing) |
| **ClickHouse `quantile`.** "The result is non-deterministic", reservoir up to 8,192 | [quantile](https://clickhouse.com/docs/sql-reference/aggregate-functions/reference/quantile) |

**Not checked, either way: whether findings or anomalies appear on charts in the free editions of OpenObserve or Uptrace.** So findings on charts must be worded as what nexovue does, and never as a difference (foundation section 12).

**What the comparison shows**

1. **Dashboards are not charged for separately by most.** They bundle them, and Grafana charges by active person. So a
   nexovue "dashboards free, no per-person charge" is parity with SigNoz and an actual difference from Grafana Cloud.
2. **Everyone with public links limits them.** Grafana and SigNoz both drop variables. Grafana warns about query load and
   annotation leakage. Grafana's advisory CVE-2023-2801 was a crash reachable through public dashboards.
3. **Importing Grafana exports is SigNoz's on-ramp.** Nexovue cannot match this honestly: a Grafana panel holds a free-form
   query, and nexovue has no query language. *Corrected in review:* it is one of several. SigNoz also has Datadog and New Relic guides, so nexovue's gap on migration is wider than this says. The decision not to import stands, at that cost.
4. **Grafana itself warns about sprawl.** Its guidance names copying as "the easiest kind of sprawl", recommends linking
   with URL parameters over copying, and keeps usage tracking in Enterprise. ~~Offering last-viewed free is a small, true
   difference.~~ *Corrected in review:* at most a difference from Grafana's open-source edition, if current docs confirm that, and **not** from Datadog, whose API exposes `viewed_at`. Keep the feature, and use it in no positioning until checked.
5. **Grafana's advice on refresh** is "avoid unnecessary dashboard refreshing". Rollups change by the minute (`rollup.py:42`),
   so a 10-second refresh on a rollup panel mostly re-reads the same data.

## 5. Methods compared

### How a panel gets its data

| Option | Good | Bad | Verdict |
|---|---|---|---|
| **A. Fixed panel types, fixed statements** | Tenant check stays provable; costs are bounded and known; numbers match other screens | Cannot answer unforeseen questions; narrower than rivals | **Choose.** Already recorded in the foundation |
| **B. Query builder compiling to SQL** (SigNoz-style) | Flexible, no language to learn | Runtime-built SQL fails the structure test (`test_tenant_chokepoint_structure.py:325`). Needs a whole compiler and its own safety proof. Cost unbounded | Not now |
| **C. Free-form SQL or PromQL** (Grafana-style) | Maximum power | Breaks the tenant rule outright (`alerts/telemetry.py:1-13`) | Never, as a hosted feature |
| **D. Each panel calls today's routes** | Almost no new backend | Fan-out problem; 30 shapes for the console to know | Only as the first step behind the batch route's interface |

A panel definition should be a kind plus checked settings, such as `{kind: "service_rate", service: "checkout"}`, never
anything resembling a query.

### Reading many panels

| Option | Good | Bad | Verdict |
|---|---|---|---|
| Each panel its own request | Simple; limits work as they are | 30 panels at 10 s spends the quota (arithmetic) | Rejected |
| Batch, counted as one | Simple | Trap 1: hides real load | Rejected |
| **Batch, merging equal statements, charging each scan** | Real saving, and limits stay honest | Most work; the middleware cannot see scan counts, so the route must take slots itself | **Choose** |
| Add a short server-side cache | Two people watching one dashboard share a read. Grafana sells this as a paid feature | Must be keyed by tenant, project, statement and rounded window, or it becomes a leak. Needs a place to live: Redis is gone (ADR-020) | Later, once measured |
| Materialized view per panel | Fast | Migration per customer choice; impossible in a fixed schema | Rejected |

**Refresh.** A dashboard should default to 1 minute (*guess*), and allow 30 s; the console's global 5 s and 10 s options
(`refresh.ts:34-40`) should not apply to dashboards. Arithmetic: 30 scans at 1 minute is 30 a minute, against 180. The
default and the panel cap must both be re-set from a load test.

### Layout

| Option | Good | Bad | Verdict |
|---|---|---|---|
| `react-grid-layout` 2.3.0 | The common choice; MIT; declares React 16.3 or later, so React 19 is accepted by its metadata but not proven | Five dependencies. Keyboard support not mentioned on its page; a comparison site says it needs extra work | Candidate |
| `gridstack` 14.0.0 | No dependencies; MIT | 2.16 MB unpacked; not React-native. GitLab found that editing mode lacked keyboard support (older report) | Candidate, second |
| `dnd-kit` plus own grid | Reported strong keyboard support | The grid itself must be built and maintained | Rejected for now |
| **Ordered rows of sized panels, using CSS grid and buttons** (move up, down, wider, narrower) | No new dependency. Keyboard and screen-reader work is natural. WCAG 2.2 criterion 2.5.7 (AA) asks for a non-drag alternative anyway. Data is simple | Less free-form; less like rivals; no drag at all at first | **Recommended first version**, adding drag on top later without changing stored layout |

Store position as a plain ordered list with a width out of 12, as SigNoz and Datadog use 12 columns. Grafana uses 24; nobody
needs that precision here. A person will miss dragging, which is the cost, and the spec reviewers should challenge this.

### Where the definition is stored

| Option | Good | Bad | Verdict |
|---|---|---|---|
| Child table per panel | Panels queryable, one row per change | Layout changes touch many rows; export needs reassembling | Rejected |
| **One checked JSON document on `dashboards`, with a `schema_version`** | Saves atomically, exports directly, simple versioning. Grafana's model has a `schemaVersion` | Postgres cannot enforce panel validity, so a validator must, on every write and import | **Choose**. Foundation left this to F20 |

Unknown fields and kinds must be refused on write. On read, a kind nexovue no longer knows must show as "this panel
type is no longer available", never crash the page.

### Who owns a dashboard

A project is an environment (`data/postgres/models.py:346-352`).

| Option | Good | Bad | Verdict |
|---|---|---|---|
| Per project | Simple; deletion cascades; tokens scoped to a project work | The same dashboard for staging and production means a copy, which is Grafana's named cause of sprawl | Rejected |
| **Per organization, displayed against a chosen project** | One definition for all environments. The address names the project (Trap 8) | A service may not exist in one project, so that panel must say so. A one-project token needs a rule | **Choose** |

Roles are organization-wide already (`auth/dependencies.py:163-185`, foundation section 5), so this adds no access model.

## 6. As an attacker

| Attack | What stops it |
|---|---|
| **Service names as attack text.** Anyone with a browser key can invent a service or endpoint name (foundation section 3, `alerts/payloads.py:204`) | React text only, in every panel, title and legend. Canvas drawing cannot run script, though chart labels must still be treated as untrusted when shown as text |
| **Stored script through a note panel** | **Note panels are plain text. No Markdown, no HTML, no images, no link rendering.** OpenObserve's HTML panel is what to avoid. Model text must never reach `dangerouslySetInnerHTML` (foundation U4), and neither should this |
| **Malicious import file** | Same validator as a write; a size cap; a refusal of unknown fields. A file can only name panel kinds, so nothing in it can run. Imported titles are free text |
| **Reading another customer's dashboard by id (IDOR)** | Every Postgres read binds `org_id`. An unknown and a foreign id answer the same 404 |
| **Pointing a panel at another customer's check or service** | Telemetry binds the tenant from the session, never the URL (`services/router.py:3-9`). Postgres-backed references, such as check ids, must be re-checked against the organization at read time |
| **Overload: a dashboard of 1,000 panels, or a year's window** | Panel cap, statement cap per batch, real slot charging (Trap 1), existing limits of 30 s, 1 GiB and 500,000 rows (`tenant_statements.py`), and a series cap |
| **A viewer writes through the batch's POST** | Draft preview is gated by `EDIT_DASHBOARDS`. Saved reads are GET |
| **Using a saved dashboard to run a heavy read as someone else** | Saved definitions hold no credentials and no raw query, and costs are charged to whoever reads |
| **Support staff browsing dashboards** | Each support-view request is already recorded for the owner (`auth/dependencies.py:133-140`) |
| **Prompt injection through dashboards** | **F4's chat must treat titles and note text as free text** (foundation section 3, U-rules) if it ever reads them. No AI link reads dashboards in this feature. AI is not needed for any part of F20, and it works with AI off |
| **Telling a false story** | A person could title a staging dashboard "Production". The displayed project must come from the data, not the title |

**AI safety.** F20 adds no AI link, so it adds no injection suite, and every part of it must work with AI off. F20's module
should join the same kind of import check as `test_detection_has_no_ai.py`, so an AI link cannot arrive unnoticed. Should AI
building ever be proposed, it needs a new decision: it would be a write driven by attacker-controlled text, with its own suite.

## 7. What we should bring, and what we should not attempt

### Bring

1. **Panel kinds, about 12** (*guess at the number; the design decides*), each one a thing nexovue already reads:
   project RED; one service's RED; a ranked services or endpoints table; dependencies; error groups over time; node trend;
   check uptime; web vitals; database statements; open findings count; one number with a change; plain note. Then
   **metric series**, the one truly new read, after Trap 5 is solved. `slo_budget` arrives from F19, and markers from F28.
2. **Start from something, never from blank.** "Start from this service" on a service page; "Copy Overview into a
   dashboard"; a small number of templates built from the kinds above.
3. **Markers and spans on every time panel.** Deploys and open findings, which is what makes these charts answer the
   question a generic chart cannot. **This is the one thing here that could feel better than a general tool**, because
   detection and deploys are already in the same product. It must stay a modest claim. *Corrected in review:* overstated. Datadog's Change Overlays put deploys on graphs from one toggle, Grafana's built-in annotations include alert state, and Netdata marks anomalies on each chart. Deploys are parity, and findings may carry a narrower claim.
4. **A shared cursor with values, and drag-to-zoom that moves every panel.** `onSelectRange` exists already (`TrendChart.tsx:110`).
5. **Anti-sprawl for free.** Owner, last viewed, sort by staleness, description field. Duplicate exists but asks for a new name.
6. **Honest panels.** Per-panel status; "last updated"; "data goes back to"; a project named in the header and address.
7. **JSON export and import**, with `schema_version`, using nexovue's own format. ~~It gives backup and version control.~~ It is not
   dashboards-as-code with a write API, because tokens read only, by design. *Corrected in review:* D9 and D39 leave no scripted read and no bulk export, so backup and version control are by hand, one file at a time.
8. **The front door.** `/` becomes the feed, Overview moves to `/overview`, and a project with no findings shows a calm summary
   with something useful, not an empty page.

### Do not attempt

| Not doing | Why |
|---|---|
| A query language or free-form SQL | Tenant rule; size of team. Grafana has spent over a decade on this |
| Grafana dashboard import | Its panels hold queries nexovue cannot run. A half-working import is worse than none |
| Public links, embeds, per-dashboard access lists, scheduled reports | Security record, query load, and no evidence of demand. Add on request. "Not this feature" already excludes embedding (`feature-catalog.md:457`) |
| Template variables | Most of their use is choosing a service or environment, which the project picker and "start from this service" cover. **The most likely thing buyers will ask for, and the first to reconsider.** A single dashboard-wide service picker is the likely smallest form |
| Plugins, custom chart code, HTML panels | Script execution in a console holding a session |
| AI generating dashboards | Foundation decision |
| Alerts from a panel | SigNoz is said to offer this. *Corrected in review:* no page was read, so `spec.md` does not repeat it until T11.5 reads one. F21 has its own conditions, and a panel's arbitrary threshold would make a second alert system. A link to "create an alert on this service" may do |
| Rewriting Overview as a stored dashboard | It works and is tested. Rewriting it gains nothing for a customer. Keep it as code; let people copy it |
| Claiming to match or beat Grafana | Foundation section 12 |

### The honest position

Dashboards here are table stakes. Nexovue's real claim stays the chain. A sales line for F20 should be modest: "Your findings
and deploys on every chart, as many dashboards as you want, with no charge per person." The first half must stay in the
future tense until built, and the second must hold only within what plan assumptions allow (foundation section 10). *Superseded in review by `spec.md`, What this brings.*

## Decisions taken

*Recommended by the authors, founder may overrule.*

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| R1 | Panel data source | Fixed kinds / query builder / free-form | **Fixed kinds.** Already the foundation's rule. Cost: rivals answer more questions |
| R2 | Batch counting | One request / each scan charged | **Each scan charged, after merging.** The limits are there to protect the store; counting one would leave them only in name |
| R3 | Batch verb | POST with panels / GET by dashboard id, POST only for drafts | **GET for saved.** Support view and the door rules both work with it (Trap 3). A small refinement of the foundation's route |
| R4 | Tier | Whole `/dashboards` / only `/dashboards/panels` | **Only the reads,** so saves use the default tier |
| R5 | Storage | Child table / checked JSON | **Checked JSON with `schema_version`.** The foundation left this open |
| R6 | Owner | Project / organization | **Organization, shown against a named project.** Avoids copies per environment. Costs a "not in this project" state per panel |
| R7 | Layout | Grid library / own rows with buttons | **Own CSS grid with move and resize buttons first.** Accessible and free of dependencies. People may miss dragging, so it is added if asked. The UX review should test this hardest |
| R8 | Note panels | Markdown / plain text | **Plain text.** Keeps a way out closed, and no renderer to defend |
| R9 | Public links | Include / leave out | **Leave out.** Add on request, with an expiry and a limit |
| R10 | Variables | Full / one service picker / none | **None at first; one dashboard-wide service picker is the first follow-up.** Most likely to be overruled by real buyers |
| R11 | Refresh | Global setting / per dashboard | **Per dashboard, default 1 minute, floor 30 s** (*both guesses*). Rollups have a one-minute grain |
| R12 | Panel cap | None / protective | **A stated protective limit, starting at 30 panels** (*guess*), as protection and not an upsell. Set from a load test. No limit on dashboard count, other than one guarding against abuse |
| R13 | Overview | Rewrite as a stored dashboard / keep, allow copying | **Keep it.** The catalog's "fixed instance" holds in spirit: same panels, same numbers |
| R14 | Token reach | Definitions and data / definitions / neither | **Definitions only, for export.** Tokens already reach the underlying read routes |
| R15 | Grouping | Any label / service plus a fixed list | **Service plus a fixed list,** as Trap 6 forces. To be measured before widening |

## Founder decisions needed

> **Superseded 2026-11-02, kept as a record.** Item 1 is answered in [`spec.md`](spec.md), D58: `/` moves as soon as F6 merges. Item 2's conversations are settled by the foundation's founder decision 8 (one shared round; not the foundation's table row FD8, which is who may ask the AI), and what their answers change is `spec.md`'s founder decision 1 (D60). The open list is in [`status.md`](status.md), *Open decisions*.

**1. When does `/` move to the Findings feed?** FD15 gives this to F20, and F20 is built last, so the front door stays wrong
through all of Stages 2 to 5. The founder's decision is that the feed is what a person lands on, and findings already exist
on this branch. It is one route, the manifest and an empty state. **Recommendation:** do it as a short change record as soon as F6
merges, without waiting for F20. It changes what every new user sees first, which is why it is yours.

**2. Is a builder still worth building, as specified, before talking to buyers?** The research already recommends five to
eight conversations. The evidence for a builder is weak, and it is the last stage, so nothing is lost by asking first.
**Recommendation:** keep F20 in Phase 2 as you decided, and make those conversations the entry condition for starting it,
including two direct questions: "Would you reject us for lacking variables, or a drag grid?" Costs your time only.

## Not checked

- Any load, memory or latency figure. Whether a merged batch really saves what the arithmetic suggests.
- Whether `series_hash` collapses instances in practice, or whether `HISTOGRAM_SQL` carries that fault.
- Whether a shared cursor works well at 30 uPlot instances. React 19 with `react-grid-layout`.
- Seeded retention values through later migrations.
- What customers want. Nobody has been asked.
- Datadog's shared-dashboard restrictions, OpenObserve's public dashboards and permissions, and Honeycomb's board limits. (Which SigNoz edition has public sharing is now settled: section 4.) Competitor prices change, and Grafana's advisory details beyond CVE-2023-2801 came from
  an aggregator and are not used here.

## Sources

**In this repository:** [`phase-2-plan.md`](../../product/phase-2-plan.md), [`phase-2-foundation.md`](../../product/phase-2-foundation.md),
[`feature-catalog.md`](../../product/feature-catalog.md), [`vision.md`](../../product/vision.md),
[code map](../../research/phase-2-code-map-2026-11.md), [competitors](../../research/phase-2-competitors-2026-11.md),
[customers](../../research/phase-2-customers-2026-11.md), [scope](../../research/phase-2-scope-2026-10-30.md),
[console redesign wireframes](../console-redesign/wireframes-dashboard.md).

**Read for this document, 2026-11-02:**

- Grafana: [pricing](https://grafana.com/pricing/), [shared dashboards](https://grafana.com/docs/grafana/latest/visualizations/dashboards/share-dashboards-panels/shared-dashboards/), [query options](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/query-transform-data/#query-options), [best practices](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices.md), [query caching](https://grafana.com/docs/grafana/latest/administration/data-source-management/#query-and-resource-caching), [JSON model](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/view-dashboard-json-model/), [CVE-2023-2801](https://grafana.com/security/security-advisories/cve-2023-2801)
- SigNoz: [manage dashboards](https://signoz.io/docs/userguide/manage-dashboards/), [templates](https://signoz.io/docs/dashboards/dashboard-templates/overview/), [public sharing](https://signoz.io/docs/dashboards/public-sharing/), [pricing](https://signoz.io/pricing/)
- Datadog: [dashboards](https://docs.datadoghq.com/dashboards/), [sharing](https://docs.datadoghq.com/dashboards/sharing/), [pricing](https://www.datadoghq.com/pricing/)
- Sentry: [custom dashboards](https://docs.sentry.io/product/dashboards/custom-dashboards/), [dashboards](https://docs.sentry.io/product/dashboards/)
- [OpenObserve dashboards](https://openobserve.ai/docs/user-guide/dashboards/dashboards-in-openobserve/), [Uptrace dashboards](https://uptrace.dev/features/dashboards), [Perses](https://github.com/perses/perses)
- Honeycomb: [pricing](https://www.honeycomb.io/pricing), [boards overview](https://support.honeycomb.io/support/solutions/articles/72000627778-honeycomb-boards-overview)
- Layout: [react-grid-layout](https://github.com/react-grid-layout/react-grid-layout) and [npm metadata](https://registry.npmjs.org/react-grid-layout/latest), [gridstack metadata](https://registry.npmjs.org/gridstack/latest), [GitLab: keyboard support in grid layout](https://gitlab.com/gitlab-org/gitlab-services/design.gitlab.com/-/issues/2885), [npm-compare](https://npm-compare.com/react-draggable,react-grid-layout,react-resizable)
- [uPlot docs](https://raw.githubusercontent.com/leeoniya/uPlot/master/docs/README.md), [WCAG 2.5.7 Dragging Movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html)
