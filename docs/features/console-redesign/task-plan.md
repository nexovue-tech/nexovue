# Task plan: console-redesign

> **Status:** Awaiting approval. Produced by `task-plan` on 2026-09-13. No file edited yet.
>
> Covers the **whole feature** — all 21 routes — so total scope is visible in files, once.
>
> **Confidence is marked per task, because it is not uniform.**
>
> | | Meaning |
> |---|---|
> | **E** | Exact. The files were read; this is what changes. |
> | **P** | Provisional. Files named from structure, not yet read. Re-expanded before that work starts. |
>
> **T1–T6 are complete and T7–T14 re-expanded to `E` on 2026-09-13**, now that the archetype survived
> contact with real data. Fourteen exact, thirty-three still provisional. The rest are re-expanded
> the same way, when their turn comes.
>
> The difference between these tasks and the **Outcome** is the measure of how well this planning
> works.

## Current system

Read from the code, not from this feature's own documents.

| What | Where | State |
|---|---|---|
| Route component and view, split so the view is a pure function of props | `ServicesScreen.tsx:46-180` | Built |
| p50 / p95 / p99 already rendered | `ServicesScreen.tsx:331-364` (`MetricCells`) | **Built** — the wireframes claimed otherwise |
| Virtualized rows, one flat index space for services and endpoints | `ServicesScreen.tsx:110-118`, `service-rows.ts:80-126` | Built |
| Loading / error / empty | `ServicesScreen.tsx:136-152` | Built, but empty is one state where the spec wants two |
| Ranking: API order, with a `slowest` escape hatch | `service-rows.ts:18-39` | Built |
| Row heights: service 36px, endpoint 28px | `service-rows.ts:46-49` | Built |
| Backend series for every service in one response | `services/service.py:70-118`, `queries.py:TREND_SQL` | **Built** — `TREND_POINTS = 60`, server-chosen bucket |
| `TrendPoint` carries `requests_per_minute`, `error_rate`, `p95_ms` | `services/schemas.py` | Built |
| Em-dash guard | `shared/format/number.ts` (`isReading`, `UNKNOWN`) | Built |
| Sparkline | — | **Missing** |
| Any hook calling `/services/trend` | — | **Missing** |
| Severity mark on a row | — | **Missing** |

## Change required

[`design.md`](design.md) §3 (the row), §6 (Sparkline), and
[`wireframes-ranked-entities.md`](wireframes-ranked-entities.md) §1. Not restated here. Where this
plan and those disagree, the disagreement is in **Questions** below.

## Impact

Whole feature. Per-area totals, so the size is visible before the first edit.

| Area | What changes |
|---|---|
| New shared components | `Sparkline` · `VirtualTable` · `TimeRangePicker` · `FilterBar` · `KeyValueList` · `StackTrace` · `JsonTree` · `SeverityBadge` · `StatusDot` · `Drawer` — ten, all named in `design-system.md` as designed-but-never-built |
| Web features touched | all 15 folders under `apps/web/src/features/`, plus `app/layout/` and `app/routes/route-manifest.ts` |
| New route | `/` Overview, and a Metrics reader |
| API domains touched | `traces` · `logs` · `errorgroups` · `synthetics` · `dbstats` · `infrastructure` · `rum` · `servicemap` · `usage` — schema and query widening for filters, deltas and unshown columns |
| Schema / migrations | **none** — no ClickHouse or Postgres change. The heatmap's alternative was rejected for exactly this reason |
| Tests | one suite per rebuilt screen, plus `Sparkline.test.ts` and a structural test for AC21 |

## Reuse

- `isReading` / `UNKNOWN` — `shared/format/number.ts`. The sparkline's "too few buckets" case uses
  the same rule as every other absent reading rather than inventing one.
- `formatDuration`, `formatErrors`, `formatRatePerMinute`, `formatCompactRate` — all present.
- `SkeletonText`, `SkeletonRows`, `EmptyState`, `InlineFailure`.
- The `ServicesView` props pattern — the sparkline data arrives as a prop, so the view stays testable
  without a query client.
- `service-rows.ts`'s row model. **Row heights do not change**: a 20px sparkline fits inside the
  existing 36px service row, so the virtualizer's size estimates are untouched.

## Gaps

1. `Sparkline` — no chart primitive exists outside the two uPlot wrappers, and neither is inline-sized.
2. `useServiceTrend` — the endpoint is served and nothing calls it.
3. A severity mark — no row carries one today.
4. The empty state is one screen where the spec requires two.

## Tasks

Ordered by `plan.md`. `E` and `P` as above.

### The thin path — `/services`, judged before anything else starts

- [x] **T1** `E` — `shared/components/Sparkline.tsx` (new). Inline SVG, no axes or legend, single
      stroke plus an emphasized endpoint. Explicit `max` so the scale is the caller's, never per-row.
      Below a minimum bucket count renders `UNKNOWN`.
      → `Sparkline.test.ts`: same series at different `max` differ; too-few renders the em dash; an
      empty series does not throw.
- [x] **T2** `E` — `features/services/api.ts`: `useServiceTrend` → `ReadonlyMap<string, TrendPoint[]>`.
      One request for the whole list. → fixture in `ServicesView.test.ts`.
- [x] **T3** `E` — `features/services/ServicesScreen.tsx`: `trendByService` prop, trend column in
      `MetricCells`, one screen-wide `max`. → `ServicesView.test.ts`: two magnitudes, one scale; a
      missing series renders the em dash and still renders its row.
- [x] **T4** `E` — the severity mark; healthy carries none; critical and warning differ by shape as
      well as hue. → `ServicesView.test.ts`.
- [x] **T5** `E` — split the empty state into never-arrived and none-in-window.
      → `ServicesView.test.ts`, one case each.
- [x] **T6** `E` — ranking test: 50% of 96 ranks below 4.1% of 1,204, and `service-rows.ts` does not
      resort under `worst`. → `service-rows.test.ts`.

**Stop. Judge the screen against the prototype before T7.**

### Widen the archetype — four screens *(re-expanded to `E`, 2026-09-13)*

**What the reading found.** All four APIs already read time-stamped rows and collapse the time axis
at the last step — `argMax(value, timestamp)` in `NODE_READINGS_SQL` (`infrastructure/queries.py:114`)
and `SLOW_STATEMENTS_SQL` (`dbstats/queries.py:55`), `GROUP BY check_id` in `CHECK_SUMMARIES_SQL`
(`synthetics/queries.py:69`). **No new table, no migration, no ingestion change** — the series is in
the rows already being scanned.

Two patterns exist to copy rather than invent:

- `synthetics/queries.py:49` defines **`_BUCKET_NANOS`**, a reusable bucket expression, and
  `PHASE_BREAKDOWN_SQL:99` already uses it. Its comment explains the integer-division form: the
  width is a bound parameter, which `INTERVAL {n} SECOND` cannot take.
- `errorgroups/queries.py:206` defines **`_GROUPED_WITH_DETAIL`**, which already assigns a
  `group_id` to every occurrence. A per-group series groups that same subquery by bucket, so it is
  the scan the list already performs, grouped differently.

**And one that does not.** Only `services` and `servicedetail` share `MetricCells`; errors,
synthetics, infrastructure and database each carry their own table markup. There is no shared row to
extend, so each screen adds the column itself. That is what makes T14 a refactor rather than a
column.

- [x] **T7** `E` — `errorgroups/queries.py`: `GROUP_TREND_SQL` over `_GROUPED_WITH_DETAIL`, bucketed
      by the same integer-division form synthetics uses; `schemas.py` gains `GroupTrendResponse`;
      `router.py` a `/trend` route. → `test_errorgroups.py`: buckets sum to the group's
      `occurrences`.
- [x] **T8** `E` — `features/errors/`: `api.ts` gains `useErrorGroupTrend`; `ErrorsScreen.tsx` gains
      the sparkline column and three-line rows; `NEW` derived from `first_seen` inside the window.
      → `ErrorsView.test.ts`.
- [x] **T9** `E` — `synthetics/queries.py`: `CHECK_TREND_SQL` reusing `_BUCKET_NANOS` over
      `METRIC_STATUS` and `METRIC_DURATION`, grouped by `bucket_nanos, check_id`. → `test_synthetics.py`.
- [x] **T10** `E` — `features/synthetics/SyntheticsScreen.tsx` on the row. **A failed run is a gap in
      the duration line, never a zero** — `Sparkline` puts an absent reading on the floor today, so
      this needs a null-aware variant or the screen must pass a sentinel. `tls.days_remaining` gains
      a column. → `check-health.test.ts`.
- [x] **T11** `E` — `infrastructure/queries.py` and `dbstats/queries.py`: add the bucket to the
      existing `GROUP BY` rather than a new statement; schemas gain the series. Infrastructure
      returns one series **per node per metric**, which is the largest response in this group.
      → `test_infrastructure.py`, `test_dbstats.py`.
- [x] **T12** `E` — merge `features/database/` into `features/infrastructure/` as two views (IA2);
      `route-manifest.ts` drops `/database` from `NavPath`, `SHELL_SCREENS` and `NAV_SECTIONS`.
      → `route-manifest.test.ts` must still pass; it is what stops the sidebar offering a dead path.
- [x] **T13** `E` — `node-readings.ts`: stale becomes **critical**, measured against
      `reporting_interval_seconds` rather than a fixed timeout, so an autoscaling fleet is not a wall
      of red; `NodeListResponse.truncated` surfaced. → `node-readings.test.ts`.
- [ ] ~~**T14** — `shared/components/VirtualTable.tsx`~~ **Removed from this feature, 2026-09-13
      (Q4).** It is a refactor with no user-visible change, and extracting a shared table from four
      examples while nine exist is guessing at a shape. It becomes its own change record after the
      screens are rebuilt, when all nine callers are known. The four screens here are touched twice
      as a result, which is the accepted cost.

### Make the screens answerable — filters and context *(re-expanded to `E`, 2026-09-13)*

**The audit, read from every router's own signature** rather than grepped — six earlier greps in
this feature returned the wrong answer, so this was parsed from the AST.

`project_id`, the time window, `limit` and `cursor` are excluded: every read takes them and none of
them is a filter a customer thinks in.

| Screen | Filters today | What a customer cannot ask | Column that already exists |
|---|---|---|---|
| **Traces** | `service` · `operation` · `status` · `min_duration_ms` · `max_duration_ms` · `attr`+`attr_value` | "traces where *this span* was slow", not just ones containing the service | `spans.duration_ms` |
| **Logs** | `q` · `service` · `min_severity` | "exclude this pattern" — only positive substring match exists | `logs.body` |
| **Errors** | `service` | "only new since the deploy" · "sort by first-seen" | `first_seen`, `last_seen` |
| **Services** | — | "only with errors" · "HTTP not queues" | `error_count`, `span_kind` |
| **Service map** | — | "hide low-volume links" · "only failing edges" · "this service and its neighbours" | `calls_per_minute`, `error_rate`, `caller`/`callee` |
| **Synthetics** | — | "only what's down" · "browser checks only" · "certificates expiring" | `up`, `check_type`, `tls.days_remaining` |
| **Database** | — | "only this server" · "slower than N" · "only this database" | `server`, `latest_mean_ms`, `namespace` |
| **Infrastructure** | — | "only containers" · "only stale" · "over N%" · "this host's containers" | `kind`, `stale`, `cpu_utilization`, `host` |
| **RUM** | — (`dimension` is a breakdown) | "only mobile" · "only this page" · "only poor readings" | `device_class`, `route`, `rating` |
| **Usage** | — | "which project is costing me this" | `ProjectDayUsage.project_id` |

**Traces accepts six filters. Seven screens accept none.** That asymmetry is invisible from inside —
traces was built first and built carefully — and from outside reads as the product losing interest
after the second screen.

**The finding that changes the work.** Three filters exist *only in the browser* and filter the page
rather than the data:

- `KindFilter` on infrastructure (`InfrastructureScreen.tsx:122`)
- the `slowest` ordering on services (`service-rows.ts:33`)
- `isNewInWindow` on errors

Each sits under a server cap — services pages at 100, errors at 50, database at 500. **A client-side
filter over a capped page silently under-reports**: ask for "only failing services" on a project with
150 of them and the answer is "the failing ones among the first hundred", with nothing on screen able
to say so. These are not conveniences to leave alone; they are wrong answers waiting for a customer
large enough to notice.

- [x] **T15** `E` — `shared/components/FilterBar.tsx` (new). One control, driven by a declared list
      of filters, so nine screens do not each invent a row of buttons. → `FilterBar.test.ts`.
- [x] **T16** `E` — API filters over columns already stored, one test each in the domain's own
      offline suite: `services` (`with_errors`, `span_kind`) · `servicemap` (`min_calls_per_minute`,
      `failing_only`) · `synthetics` (`down_only`, `check_type`) · `dbstats` (`server`,
      `min_mean_ms`) · `infrastructure` (`kind`, `stale_only`, `host`).
- [x] **T17** `E` — `rum`: `device_class`, `route` and `rating` become filters as well as breakdown
      dimensions. `BreakdownDimension` stays two members — `country` has no producer (AC4).
- [x] **T18** `E` — **move the three browser-side filters onto the server** and delete the local
      ones, so a filtered list is the filtered data rather than a filtered page. → a test per screen
      that a match beyond the first page is still returned.
- [x] **T19** `E` — filtered-empty distinguished from never-had-data on every screen that gained a
      filter (AC15). `ServicesScreen` already splits these two; the others copy that shape.
- [x] **T20** `E` — previous-period delta: a second read over the shifted window, one threshold per
      measure, rendered **only where significant** (AC13, AC14). → tests that an unremarkable reading
      carries no annotation, and that an absent prior window renders no delta rather than a zero.
- [x] **T21** `E` — a structural test that no feature interprets an HTTP status itself (AC21).

**A reasonable stopping point. Five screens rebuilt, filterable, carrying context.** Reached
2026-09-13.

**What changed from the plan.** Three things the audit got wrong, each found by reading the code:

- **`isNewInWindow` was never a filter.** It marks a row; it has never hidden one. The real gap was
  that "only new" could not be asked at all, so T18 *added* it rather than moving it — and
  `started_in_window` now comes back on the row, so the marker and the filter share one definition
  instead of keeping a copy each.
- **RUM has no rating to filter on.** `rating` is a column of `web_vitals`, not of `web_vitals_1m`
  which this screen reads. A rating is a property of a value and the p75 is a new value, so the
  rating a reader sees is computed on the screen — `rum/service.py` already refuses to hold a second
  copy of those thresholds. Filtering on the raw per-reading rating would have excluded pages this
  screen still labels poor. `device_class` and `route` were built; the rating filter was not, and the
  reason is recorded in both `rum/queries.py` and `features/rum/filters.ts`.
- **Infrastructure's per-kind counts went with its filter.** They counted the page, which is the
  thing being fixed.

**T20's thresholds were measured, not chosen.** At a fifth, 14 of 15 services carried a request-rate
delta over six hours and 6 of 16 over a day — a screen of annotations that has stopped saying
anything. A volume now needs to move by half; a duration still needs a fifth, and an error rate is
measured in points because 0.1% becoming 0.2% has doubled and means nothing.

### Investigation core — five screens *(re-expanded to `E`, 2026-09-13)*

**Read from the code and the store, not from the wireframes.** Three of the eight tasks shrank or
vanished, and two approved documents turned out to be wrong.

#### What the reading found

| Claim | Where it came from | What the code says |
|---|---|---|
| `TraceSummary` does not expose `worst_severity` | `wireframes-investigation.md` gap table | **Wrong.** It is `status_code`, mapped through `_SEVERITY_NAMES` at `traces/service.py:519,537` — exactly as `traceassembly/summary.go:13` says the API should. |
| `ServiceEdge` needs the latency the rollup stores | same file, line 220 | **Wrong, and already withdrawn** at the top of that file. `servicemap/schemas.py:54-56` carries `p50_ms`, `p95_ms`, `p99_ms`. |
| The service map needs a volume floor and failing-only | T28 | **Built in T16.** `servicemap/queries.py` has `MIN_CALL_COUNT_CLAUSE` and `FAILING_ONLY_CLAUSE`. |
| `callee_kind` labels the node, `edge_kind` marks a queue | T28 | **Built.** `service-graph.ts:216-218` and `:311,322`. |
| One real chart on service detail | T27 | **Built.** `servicedetail/TrendChart.tsx` is uPlot. |
| Live shares the search filters | T24 | **Built.** `useLogTail(projectId, filters, true)` at `LogsScreen.tsx:62`. |
| The trace arrow appears only with a `trace_id` | T24 | **Built.** `LogsScreen.tsx:499`. |

**`wireframes-investigation.md` carries a botched edit.** Its 2026-09-13 correction is followed by a
stray `---|---|` and a duplicate of the table it replaced, still asserting the withdrawn `ServiceEdge`
claim. Line 220 repeats it. Both are listed as T22's first act, because an approved document that
contradicts itself is worse than one that is merely out of date.

**`span_services` and `span_endpoints` already filter.** `SUMMARY_SERVICE_CLAUSE` and
`SUMMARY_ENDPOINT_CLAUSE` (`traces/queries.py:206,218`) mean "which traces touched payments" is
answerable today. What is missing is *showing* them: the columns are never selected into the
response, so a row cannot say what it touched.

**What is genuinely stored and unshown**, verified against the store over six hours of real spans:

| Column | Populated | Shape |
|---|---|---|
| `spans.exception_type` / `exception_message` | 380 of 120,049 | `LowCardinality(String)` / `String` |
| `spans.deployment_version` | 46,924 | `LowCardinality(String)` |
| `spans.events` | 11,446 | JSON string: `[{timestamp, name, attributes}]`, exception events carry a full stacktrace |
| `spans.links.*` | 284 | **three parallel arrays** — `trace_id`, `span_id`, `attributes` — not one field |
| `trace_summaries.span_services` / `span_endpoints` | every trace | arrays; endpoints join service to operation with `\x1f` (`summary.go:25`) |
| `GET /deploys` | served | consumed by no screen |
| `LogsForTraceResponse.TraceLink` | served | consumed by no screen |

#### Tasks

- [x] **T22** `E` — `traces/schemas.py` + `queries.py` + `service.py`: `SpanDetail` gains
      `exception_type`, `exception_message`, `deployment_version`; `TraceSummary` gains
      `span_services[]` and `span_endpoints[]`, the latter split on `\x1f` into `(service, operation)`
      pairs rather than shipped as joined strings. **Not `worst_severity`** — that is `status_code`.
      → `test_traces.py`: a span carrying an exception exposes both halves; an endpoint pair
      round-trips its separator; a trace with no exception exposes empty strings, never null.
      **Also fixes `wireframes-investigation.md`** — the duplicated fragment and line 220 — because
      the same reading produced both, and a document that contradicts its own correction should not
      outlive the commit that discovered it. No test: it is prose.
- [x] **T23** `E` — `features/traces/SpanDetail.tsx`: `exception_type` and `exception_message` lead
      the pane, above attributes (AC17). `deployment_version` is an entry in it. → `SpanDetail.test.ts`:
      a failed span shows the exception first; a span without one shows no empty section.
- [x] **T24** `E` — span `events` as a timeline and `links` as navigation in the same pane.
      `traces/schemas.py` parses `events` from its JSON string and zips the three `links` arrays into
      one list; a link whose target is not held renders as text, never a dead link.
      → `test_traces.py` for the parse and the zip, `SpanDetail.test.ts` for the two states.
      **The stacktrace inside an exception event is large** — `errorgroups/queries.py:152` already
      refuses to select one into a list read, and this must stay a detail-only field for the same
      reason.
- [x] **T25** `E` — `features/logs/LogsScreen.tsx`: facets carry their counts. `ServiceFacet.records`
      and `SeverityFacet.records` (`logs/search.py:201,206`) are served and dropped at
      `LogsScreen.tsx:73`. → `LogsView.test.ts`: a facet shows its count; a facet with none is absent
      rather than showing zero.
- [x] **T26** `E` — `features/traces/TraceListScreen.tsx`: a duration bar on one window-wide scale,
      the same shared-ceiling rule `Sparkline` uses — per-row autoscale would make every trace look
      equally slow. → `TraceListScreen.test.ts`: two rows at different durations draw different
      widths against one ceiling.
- [x] **T27** `E` — `features/traces/TraceScreen.tsx`: consume `GET /logs?trace_id=` so a trace shows
      the logs emitted under it, and render `TraceLink.exists: false` as its own state — "we hold the
      logs but not the trace" is a fact, not a failed load. → `TraceScreen.test.ts` for the three
      states: linked, held-but-absent, and none.
- [x] **T28** `E` — deploy markers on service detail (IA3). `GET /deploys` is served and drawn
      nowhere; a vertical rule on `TrendChart` at each deploy answers "did a deploy cause this"
      without leaving the screen. → `TrendChart.test.ts`: a marker lands at its timestamp's x; a
      deploy outside the window draws none.
- [x] **T29** `E` — selection travels (IA1): `shared/selection.ts` gains an entity scope beside the
      project and range, applied on arrival rather than re-chosen. Today it holds `projectId` and
      `rangeId` only. → `selection.test.ts`: a scope survives a screen change; an unknown scope is
      dropped rather than filtering everything out.

**Deleted from this block, with reasons above:** the service map task entirely (T28 as written — its
four items are built), `worst_severity` from the schema task, the `ServiceEdge` latency backend item,
and the three log items that already work. **One chart** on service detail is built; the deploy
marker it was bundled with is not, and became T29 on its own.

**Outcome, 2026-09-13.** Built, `just check` green — 2364 Python, 719 web.

Three things changed from the plan, each because the code said so:

- **T27's `exists: false` was already built**, at the destination rather than the source.
  `TraceNotStored` renders exactly that state — "this trace was not kept", no retry, no error — and
  the logs list cannot know per line whether a trace is held without a lookup per row. What T27
  actually added is the other half: a trace now shows the logs emitted under it, collapsed, because
  the waterfall is the screen and a panel that opens itself competes with it.
- **T24's links are navigation, not text.** The task said a link whose target is not held should
  render as text. Following one is already safe: `TraceScreen` answers "not kept" rather than
  failing, which is what `TraceScreen.test.ts` exists to hold. Rendering text instead would have
  removed a working path to avoid a failure that does not happen.
- **T29 kept the scope out of the selection store.** A sticky global scope would have meant a reader
  who opened `checkout` this morning finding Logs filtered this afternoon with nothing on screen
  saying why, and no way to hand the view to a colleague. It travels in the link instead — the same
  rule the filters follow — which required logs and error groups to read their service from the URL
  at all.

**One defect found in the browser, not by a test.** Arriving at Logs scoped to `frontend` — a
service with no logs in the window — left the control reading "All" while the list was narrowed to
it. A native select shows its first option when none matches its value, so the control and the
results disagreed with nothing on screen to say so. `FilterBar` already guards this; the logs
screen has its own select and did not. Fixed, with the test naming where it was found.

### The dashboard surface *(re-expanded to `E`, 2026-09-13)*

#### What the reading found

| Claim | What the code says |
|---|---|
| Service detail needs "the full RED set: distribution, operations, dependencies" | **Three of the four are built.** `TrendChart` draws rate, failure share and p95; `useServiceEndpoints` is the operations table; `useServiceDependencies` reads `service_edges_1m`. The distribution is the heatmap **OD7 resolved as out of scope** — it cannot be drawn from three stored quantiles and a post-sampling `duration_ms`. |
| The duration panel shows p50/p95/p99 | **It shows p95 only.** `TrendPoint` carries `p95_ms` alone (`services/schemas.py:52`) even though `TREND_SQL` already computes the whole quantile array and `_to_trend_point` throws two of them away (`service.py:79`). That is the one genuinely unbuilt part of the panel set. |
| Overview reuses the ranked-entity row | It does, but **the numbers above it cannot come from that list.** `/services` is capped at a hundred, so a project total summed from it under-reports the moment a customer has a hundred and one. |
| `route-manifest.ts` needs its "never the front door" comment updated | **Done in T45/T46.** What remains is the nav itself: `/` has a route and **no `NAV_SECTIONS` entry at all**, so Findings is unreachable from the sidebar today. |

**A project-wide trend has to be a server read.** Merging quantile states across services is exactly
what storing states rather than numbers is for, and `quantilesMerge` over the window gives the
project's true p95. Averaging per-service p95s in the browser would be the error this codebase
warns about in four places — "a p75 cannot be averaged out of smaller p75s". So Overview cannot be
composed from reads that already exist; it needs one of its own.

**The bottom strip costs three reads on the front door.** Synthetics and RUM are small. The
infrastructure line is a full node read — capped at 50,000 series — for a count and a worst case.
Reused rather than given a counting endpoint of its own, because a second way to count nodes is a
second answer to "how many nodes"; if it proves too slow the fix is a narrower read, not a cache.

#### Tasks

- [x] **T30** `E` — `services/queries.py` + `service.py` + `router.py`: `GET /services/overview`
      returning the project's RED over the window and bucketed across it — `quantilesMerge` grouped
      by bucket alone, never per service. → `test_services.py`: the aggregate is not the sum of the
      list's rows; a window with no rollup is an empty answer, not a zeroed one.
      `test_services_live.py`: the project p95 read whole differs from the mean of its services'.
- [x] **T31** `E` — `features/overview/` (new) + route at `/`: three RED panels from T30, one deploy
      marker spanning all three (reusing `deploy-markers.ts`, not a second reader of `/deploys`),
      the ranked-entity row reused from `features/services`, and a strip of one line per surface.
      → `OverviewView.test.ts`: all six states; a surface with no data shows an em dash, never a
      zero; the marker is drawn on every panel or on none.
- [x] **T32** `E` — `route-manifest.ts`: Overview above Signals and **Findings gains its first
      sidebar entry** — it has a route and no `NAV_SECTIONS` item, so nothing in the app navigates
      to it. → `route-manifest.test.ts` already fails on a nav path the router does not serve; add
      that every *served* screen with a title is reachable, which is the direction it does not yet
      hold.
- [x] **T33** `E` — the duration panel becomes p50/p95/p99. `TrendPoint` gains `p50_ms` and
      `p99_ms`; `_to_trend_point` stops discarding them; `trend-series.ts` draws three lines on one
      scale. → `test_services_trend.py` for the two new fields, `trend-series.test.ts` that the
      three lines share one axis — three separately-scaled lines would make p50 and p99 look equal.
- [x] **T34** `E` — `features/infrastructure/`: USE panels per node from `NodeSummary` —
      utilization for CPU, memory and filesystem, plus the two byte rates. **Labelled U and E, not
      USE**: nothing stored carries saturation — no queue depth, no run queue, no IO wait — and
      relabelling utilization as saturation would be inventing a measurement. → a test that the
      panel set names what it cannot show rather than omitting it silently.

**T34's premise was wrong, and the founder chose to widen it (2026-09-13).** The task said to label
the panels U and E because "nothing stored carries saturation". That is true of `NodeSummary` and
false of the store: `system.disk.pending_operations` — disk queue depth, USE's canonical saturation
measure — is collected with a peak of 256, alongside `system.swap.utilization`,
`system.network.errors` (41,998 points) and `system.network.dropped`. Eighteen host metrics were
ingested and read by nothing. All are standard `hostmetrics` and `docker_stats` scrapers, so any
customer already running a collector is already sending them.

Writing "saturation: not measured" would have been wrong in the most damaging direction — telling a
reader a thing cannot be done while the data sits in the store. The frame is now complete: U from
the existing columns, S and E as sentences above the table, because both are usually absent and a
column of nothing is rows of nothing.

**Rate limiting, found in the browser and worth knowing.** `/services`, `/infra`, `/synthetics` and
`/rum` are all in the telemetry tier: **60 requests a minute, four in flight** (`ratelimit/policy.py`).
Overview wanted five telemetry reads at once, and a fifth concurrent request is *refused*, not
queued — so one read failed on every cold load, and which one lost was a race. The strip now waits
for the panels: two reads, then three.

What remains is a rate rather than a defect. The live window re-buckets every ten seconds, so an
idle Overview refetches five telemetry reads six times a minute — **30 of the tier's 60**. Two tabs
on this screen exceed it. **Open for the founder:** whether the telemetry tier is sized for a
dashboard, whether the live window should tick more slowly, or whether the strip should become one
read after all.

**Deleted from this block:** the distribution panel (OD7, already decided), the operations and
dependencies tables (built), and the `route-manifest` comment (T45/T46).

### Surfaces and account *(re-expanded to `E`, 2026-09-13)*

Six tasks became three. Two were built, one was pointed at the wrong screen, and the largest turned
out to be half the size the wireframe assumed.

#### What the reading found

| Claim | What the code and the store say |
|---|---|
| RUM needs `readings` as a column, `is_route_template` labelled, ratings in words | **All three are built.** The card shows the count and refuses a percentile under ten (`vitals.ts:98-121`); the breakdown draws a `route` tag (`RumScreen.tsx:436`); `RATING_WORDS` names every rating. A thin breakdown cell already renders a dash with a footnote saying why. |
| Usage needs `settled: false` rendered and `services` beside `billed_services` | **Built.** `describeCounting` renders the unsettled days, and the cap is `max_billed_services` — a plan ceiling, not a second count — which `describeCeiling` already states as "At most N services are billed." |
| Widen `infrastructure/signals` into a metric catalogue | **Wrong home.** That endpoint answers *"which scrapers are on for this fleet"* and feeds the infrastructure strip. A catalogue answers *"what is my application instrumenting"*. Same shape, different scope; widening the first makes it answer a question the infrastructure screen never asks. |
| `metrics_histogram`, `metrics_exp_histogram`, `metrics_summary` are written with no reader (AC5) | **True, and the only references anywhere are purge and row-count.** But two of the three are **empty**: nothing in the system sends an exponential histogram or a summary. `metrics_histogram` holds **579,226 rows across 41 metric names** in a day. |
| Settings is four screens | **Six files carry a form**: `ApiKeysScreen`, `SiteKeysSection`, `ProjectsScreen`, `ProjectSettings`, `BillingScreen`, `ProfileScreen`. Their dangerous states are already built — `ApiKeysScreen` confirms a leak, `ProjectSettings` confirms disabling a signal. |

**The histogram data is real and well formed**: `k6.http_req_duration` carries sixteen buckets with
explicit bounds `[0, 5, 10, 25, 50, 75, …]`, a count, a sum and a unit. A reader can be built against
it and tested against it today.

**AC5's minimum is smaller than the wireframe assumed.** It raised "could `metrics_summary` and
`metrics_exp_histogram` be listed-but-not-drawn in a first pass" as a founder call. The store answers
it: there is nothing in either to draw. The catalogue lists whatever arrives; the reader is for
histograms, because histograms are what exists.

#### Tasks

- [x] **T35** `E` — `metrics/` (new API): `GET /metrics` — a catalogue across all five metric tables,
      one row per metric name with its shape, series count, point count and last seen. Its own
      endpoint, not a widened `infrastructure/signals`: that one is scoped to the host-metric names
      and is read by the infrastructure strip. → `test_metrics.py`: a name present in two tables is
      one row naming both shapes; an empty table contributes nothing rather than a zero row.
- [x] **T36** `E` — `GET /metrics/{name}/histogram` — buckets merged across the window over their
      own `explicit_bounds`, with `count`, `sum` and estimated percentiles. **Refuses to merge
      points whose bounds differ**: two histograms with different bucket layouts are not one
      distribution, and adding their counts silently invents a shape. → `test_metrics.py` for the
      merge and the refusal, `test_metrics_live.py` against `k6.http_req_duration`'s real sixteen
      buckets.
- [x] **T37** `E` — `features/metrics/` (new) + route + sidebar entry: the catalogue as a ranked
      list, and a histogram drawn over its own bounds when one is selected. Percentiles labelled as
      **estimates**, because a percentile off buckets is one. A metric whose shape has no reader
      lists with its counts and says so rather than being hidden. → `MetricsView.test.ts` for the
      six states and for the estimate labelling.
- [x] **T38** `E` — unsaved-changes warning (AC19), on the forms that *have* a draft to lose:
      `ProjectSettings`, `ProfileScreen`, `SiteKeysSection`. The other three submit immediately —
      creating a key or a project has no unsaved state, and warning there would train the warning
      out of being read. → one test per guarded form, plus one asserting the unguarded three do not
      warn.

**Deleted from this block:** RUM (built), Usage (built), the `infrastructure/signals` widening (wrong
screen), and the settings rework (the dangerous states are already there; only the missing warning
was real, and it is T38).

### Gateway and the Findings shell

- [x] **T39** `E` — `features/onboarding/OnboardingScreen.tsx`. **Half of this was already built**:
      the confirmation already named the service that arrived and already was the destination. What
      was missing were the two onward actions the wireframe commits — an uptime check and the cost
      question, which Flow B found the Indie persona reaches for next. The trace action now opens the
      trace itself rather than the list, which the first-traces carve-out guarantees is findable.
- [x] **T40** `E` — **already implemented; nothing to do.** `AuthScreenLayout` is the Gateway
      archetype and login, signup and all three recovery screens already use it; `/onboarding` sits
      outside the shell on the same frame with a wider measure for its code block. Verified by
      reading all six, not by the plan's say-so.
- [x] **T41** `E` — `features/findings/`: `ordering.ts` (active before resolved, then severity, then
      recency — F1 AC1.5, with resolved findings ordered by when they resolved), `api.ts` (the
      `Finding` shape and a feed that asks nothing, because no engine produces one), and
      `FindingsScreen.tsx`. The card's contents stay provisional and are marked so. The route stops
      being a placeholder — and it was the last one, so `placeholder.tsx`, `ComingSoon.tsx` and the
      manifest's screen tables went with it. → `ordering.test.ts`, `FindingsView.test.ts`.
- [x] **T42** `E` — the healthy state carries Overview's RED summary so "normal" is checkable, and
      shares no wording with failed-to-load (AC18). → a test asserting the error state contains none
      of the healthy state's reassurance, phrase by phrase, the summary numbers included.

### Closing

- [x] **T43** — `/review-diff`, run over the phase's diff. Findings below under *What the review
      found*.
- [~] **T44** — Design QA. **Partial, and deliberately reported as partial.** Every screen built in
      the final block was opened against the running stack with real data, which is where three
      defects came from that no test could reach. A pass over *every* rebuilt screen against its
      wireframe was not done — the gate still has no implementation, and one person clicking through
      twenty-one routes is not the same thing as having one.
- [x] **T45** — `docs/architecture/frontend.md` updated to current truth. It claimed a database
      *screen* that is now a view inside infrastructure, and predated `Sparkline`, `FilterBar`,
      `Delta` and the rule that narrowing a list is the server's job.
- [x] **T46** — the superseded stance, in **five** places rather than the three the audit found:
      `vision.md`'s one-line vision and its Decisions Log, `feature-catalog.md` F20 *and* its own copy
      of the vision line, and the comment in `route-manifest.ts`. The Decisions Log keeps the 2026-08
      row and marks it superseded rather than rewriting it — a decisions log that edits its own
      history cannot be used to find out why something changed.
- [x] **T47** — **Outcome** filled in; `status.md` final.

---

## Follow-up phase — reopened 2026-09-14

**The phase closed on 2026-09-13 and was reopened the next day.** A founder-led pass over every
screen, read as a customer rather than as the person who built it, found that the console is
*correct* and does not yet *behave* like a product somebody uses all day. Nothing below is a defect
in what was built; all of it is what the building did not cover.

Four decisions were taken before this list was written, and they are what bound it:

| Question | Decision (2026-09-14) |
|---|---|
| Is nexovue a team product? | **Yes, and not now.** Invites, members and roles become their own feature. Out of this phase entirely, despite the schema already carrying `owner/admin/member/viewer`. |
| Who are the setup docs for? | **Both** — someone already exporting OpenTelemetry, and someone who has never instrumented anything. Two audiences, one set of docs. |
| Global time window, or per screen? | **Global with an override.** The window travelling between screens is what makes an investigation work (IA1); the override exists for the screen that genuinely needs its own, and must be visibly an override rather than a silent divergence. |
| Is Findings honest with no engine? | **Yes for now.** The insight engine is the next feature; the healthy state stands until it exists. |

### What the reading found

| Claim | What the code says |
|---|---|
| Screens refresh in the background | **No. Every refresh is a cold load.** `useLiveWindow` advances `to` every ten seconds, `timeWindow` is part of every query key, and no query sets `placeholderData`. A changed key is a new query with no cached data, so `isPending` is true and the screen renders its skeleton. Locally that is a flicker; on a customer's latency it is the whole screen tearing down every ten seconds, for ever. |
| A customer can look at a past incident | **No.** `TIME_RANGES` is five relative options ending at 7 days. There is no absolute range, so retention the customer pays for — 30 days of traces, 395 of metrics — cannot be reached from any screen. |
| A customer can hold a screen still | **No.** Nothing pauses polling and nothing sets its interval. Reading carefully, screenshotting, or showing a colleague all fight a ten-second tick. |
| The time control is global | **Almost.** Usage carries its own 7/30/90-day control while the top bar still reads `LAST hour`, which silently does nothing there. Two controls with the same label, disagreeing. |
| Selection survives a reload | **No.** `selection` is a module-level variable; only the theme reaches `localStorage`. Reload returns to the default project and `last hour`. |
| ⌘K searches | **No.** `CommandPaletteMount` renders a *disabled* input whose placeholder reads *"mounted by P1-63, filled by a later task"*. A customer who presses ⌘K is shown an internal task id. |
| The sidebar tells a customer something useful | `phase 1 · shell only`, printed on every screen. True while most routes were placeholders; now stale, and it reads as "this product is unfinished". |
| Metrics can be narrowed | **No.** 314 names, ranked by traffic, no filter. AC15 named "the seven screens that have none" before this screen existed, so it was never in scope — and T37 did not notice. |

### The design rule these share

**Never move what someone is reading; mark what changed.** A live screen does not need loud updates
if it keeps a memory of what changed — loud updates are what an interface builds when it forgets.
That rule is what separates T48–T54 from "make the refresh quieter".

### Tasks — live data, time, and control

- [ ] **T48** `P` — keep previous data across the window tick, so a refresh is a background fetch
      rather than a remount. Everything below depends on this one. → a test that the rendered rows
      survive a window advance.
- [ ] **T49** `P` — refresh control in the top bar beside the window: pause, and an interval. **The
      paused state must be unmistakable** — a persistent "Paused · showing 14:32:05", not an icon.
      A paused screen showing stale numbers during an incident is the AC18 failure again.
- [ ] **T50** `P` — an absolute window disables polling by itself. Nothing new can arrive in a window
      that ended last Tuesday.
- [x] **T51** `E` — freeze list ordering while the pointer is over a list or a row is expanded, and
      offer an explicit "reorder" when the ranking has moved. Rows changing places under a reaching
      cursor is the real damage, not the flash.
- [x] **T52** `E` — highlight the **cell** that changed, briefly, honouring `prefers-reduced-motion`.
      Not the row.
- [x] **T53** `E` — **already built, and better than this asked for.** The logs tail pauses
      *server-side* when a reader scrolls away, so rows cannot move under them; the server holds
      lines, reports how many it dropped, and the screen renders a `GapRow` saying so **in the
      place the gap happened** rather than a count in a pill. Verified by reading
      `use-log-tail.ts` and `LogsScreen.tsx`. Traces is a paged list and Findings has no engine,
      so neither is a live feed to protect.
- [ ] **T54** `P` — one "updated Ns ago" freshness indicator, in the top bar rather than per panel.
- [x] **T55** `E` — absolute start/end in the global window control, which is what makes paid
      retention reachable.
- [x] **T56** `E` — persist the selected project and window across a reload.
- [x] **T57** `E` — a per-screen window override, rendered as visibly an override with a way back to
      the global one.
- [x] **T58** `E` — resolve Usage's competing control. A billing month is a genuinely different axis
      from an incident window, so this may stay an exception — it must look like one.

### Tasks — stop looking unfinished

- [x] **T59** `E` — delete `phase 1 · shell only`, and hide ⌘K until search exists rather than
      showing a customer a disabled box naming an internal task.
- [x] **T60** `E` — filters on Metrics: name, shape, and readable-only. The gap T37 left.

### Tasks — what the screen-by-screen read found

- [x] **T61** `E` — a warning tier for services. One error in a hundred thousand requests currently
      draws the same mark as forty percent failing. **Blocked on the threshold**, which is a founder
      decision and is recorded under Questions.
- [x] **T62** `E` — a "has this project ever reported" signal, replacing the widest-window heuristic
      that reads a project silent for eight days as one that never reported.
- [x] **T63** `E` — export a filtered result from logs and traces. The commonest ask after search.
- [ ] **T64** `P` — an active-session list beside "sign out everywhere", and second-factor
      authentication. Today a customer can end every session but cannot see what is signed in, which
      is the thing they actually want when they suspect somebody else is.
- [x] **T65** `E` — link an error group to the deploy that preceded it. `deploy_events` is ingested,
      stored, served, and consumed by markers only.

### Tasks — raised here, and possibly not this phase's work

**Both of these are in this list because the review found them, and both arguably belong elsewhere.
Flagged rather than decided.**

- [ ] **T66** `P` — billing: an invoice list and download, the payment method on file, and a plan
      change path. Today "This organization already has an active subscription" is a dead end, and
      there is no way to obtain an invoice — usually a tax requirement rather than a convenience.
      **Belongs to `docs/features/usage-billing/` more than here**, and needs the payment provider.
- [ ] **T67** `P` — customer setup documentation, for **both** audiences: a service already exporting
      OpenTelemetry, and one that has never been instrumented. Per language, plus Docker,
      Kubernetes, ECS, Lambda and bare metal; the Collector; the browser SDK. **This is not a console
      change and has no home in the repository yet** — the largest gap the review found, and the one
      that loses a customer silently, because they never reach a screen anybody is watching.
- [ ] **T68** `P` — onboarding links to those docs, and answers "nothing has arrived" with something
      better than a spinner. Depends on T67.

## New risks

**The screen-wide `max` is computed from the loaded page, not the project.** With a `limit` of 100
services the scale is correct for what is shown, but adding a 101st taller service would rescale
every sparkline. Acceptable here and worth remembering when filters arrive, which change what is
loaded.

**Forty-eight tasks is the real size of this feature**, and only six are exact. That ratio is the
honest picture: the thin path is known, and everything after it depends on whether the archetype
survives. The two tasks with no code behind them at all — T44's Design QA gate, and T37's Metrics
screen — are the ones most likely to grow.

**`useServiceTrend` doubles the screen's requests** — list and trend, in parallel. Both are bounded
and cached on the same key shape, but a slow trend must not block the list from rendering: the rows
appear with em dashes and fill in.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| Q1 | ~~`formatErrors` renders zero and unknown identically~~ | **RESOLVED 2026-09-13** | `docs/design/principles.md` rule 2 |
| Q2 | ~~rate or error rate?~~ **RESOLVED 2026-09-13: the colour picks the series.** A failing row plots its error rate, a healthy one its request rate, so a red line always means errors. Verified live: `flagd` now draws `flagd error rate` in `stroke-critical-solid`. | Resolved | [`design.md`](design.md) §6 |
| Q3 | ~~ten of sixteen sparklines nearly flat~~ **RESOLVED 2026-09-13: left as designed.** The flat lines are true — those services really are quiet — and the alternative is the per-row scaling the archetype exists to refuse. | Resolved | [`design.md`](design.md) §6 |
| Q4 | ~~`VirtualTable` in this group or its own change?~~ **RESOLVED 2026-09-13: later, its own change.** Zero user-visible benefit, and a shared table extracted from four examples while nine exist is a guess. | Resolved | [`plan.md`](plan.md) |
| Q5 | ~~a failed run drawn as a gap or a zero?~~ **RESOLVED 2026-09-13: `Sparkline` gains a gap mode.** RUM page timings and database query durations will both want it, so it belongs in the component rather than worked around in one screen. | Resolved | [`design.md`](design.md) §6 |
| Q6 | **At what error rate does a service stop being healthy?** T61 cannot be built without a number. Today one error in a hundred thousand draws the same mark as forty percent failing, so the mark has stopped carrying information. A fixed pair of thresholds is recommended — 1% warning, 5% critical, **stated on screen** so a reader knows what the mark claims — because a baseline-relative answer is really the insight engine's job. | **Blocks T61** | [`spec.md`](spec.md) Open decisions |
| Q7 | **Do T66 (billing) and T67 (customer docs) belong to this phase?** Both were found by the 2026-09-14 review and neither is a console screen: billing needs the payment provider and has a workspace of its own, and setup documentation has no home in the repository at all. Kept here so they are not lost, flagged so they are not absorbed by default. | Not blocking — T48 onward proceed either way | this table |

**Q2 and Q3 were what the thin path was built to find.** Both were visible only against real data —
sixteen services where two carry all the traffic — and neither would have surfaced from the
prototype's eight invented rows.

**Q2's fix needed two ceilings, not one.** An error rate is a fraction and a request rate is a count
per minute, so no single number scales both. Each kind now shares a ceiling with its own kind, which
is the only comparability that ever meant anything: `checkout`'s request rate was never comparable
to `flagd`'s error rate. One shared scale per screen was the right rule and it was one rule short.

**Q1 resolved: the code stands, and the principle now names the exception.** The ambiguous case
cannot occur — `error_count` is `number` in the contract and `int()` from a SQL `SUM` on the server,
so a service in a list always has a real count and the em dash there always means zero. The
exception is recorded with its condition: if `error_count` ever becomes optional on any endpoint
feeding this cell, the em dash becomes ambiguous and the formatter must change in the same commit.

**T4 and T5 are unblocked.**

## Validation

`just check`, output and counts read. New tests are Vitest, colocated.

Phone width at 375px checked on the rebuilt screen — the sparkline column is the first thing that
would overflow.

The built screen compared against [`prototype-ranked-entities.html`](prototype-ranked-entities.html).
**Nothing performs this check today** — `plan.md` names a Design QA gate and no skill implements it,
so this is a manual look.

## Outcome

**The whole phase is complete, 2026-09-13.** `just check` exit 0: **2438 Python** (1 skipped — the
billable mail suite, which is opt-in), 120 browser-SDK, **805 web across 64 files**. Against the
phase's own starting point of 2277 Python and 630 web across 48 files, that is 161 Python and 175 web
tests added across sixteen files.

The record below is kept in the order the work happened, because the difference between what each
block planned and what it actually cost is the most honest estimate anyone has of how well this
planning works.

### T1–T6 — the thin path

`just check` exit 0: 2277 Python, 120 browser-SDK, **630 web across 48 files** — up from 613/47, so
17 tests were added.

#### What changed from this plan

| Planned | Actual |
|---|---|
| Four files | **Six** — `Sparkline.tsx` and `Sparkline.test.ts` were counted as one line in Impact |
| T4 draws critical *and* warning, distinguished by shape | **Critical only.** No error-rate threshold for a "warning" service exists anywhere in the code, and inventing one would have decided product behavior in a component. The mark is binary, exactly as the errors cell beside it already was. Recorded as a question below |
| T5 splits on whether data ever arrived | **Splits on whether the widest range is already selected.** No API says whether a project has ever received telemetry. The proxy is wrong in one direction only — a project silent for over seven days reads as never having reported — which costs a reader a sentence, where the opposite error would send a customer with working instrumentation back to setup |

#### What the work found

- **`Overrides` in `ServicesView.test.ts` had no `windowLabel`,** and vitest does not typecheck, so
  the tests passed while the build failed. `lint-web`'s own comment predicted exactly this: *"biome
  does not typecheck, so without this a type error passes every gate."*
- **One pre-existing test asserted the superseded empty-state copy** and was updated rather than
  deleted — its intent still holds, only the wording moved.
- **jsdom does no layout**, so no test could have caught an invalid Tailwind class. The four new
  utilities were verified against the built CSS instead: `size-1.75` resolves to 7px, matching the
  prototype exactly.

#### Left for the founder

- **A warning tier for services.** Today a service is failing or it is not. Whether an error rate
  below some threshold should read as warning rather than critical is undecided, and the shape
  distinction in `design-system.md` waits on it.
- **A "has this project ever reported" signal.** It would replace the heuristic in `EmptyServices`
  with a fact.

#### Design QA — T1–T6, against the running app

Opened `/services` in the running stack, both themes, and measured the layout rather than judging it
by eye. Sixteen real services from the demo stack.

**Verified.** The severity dot measures exactly 7×7, the prototype's size, and appears on 2 of 16
rows — only the two with errors. Both themes render from the tokens. No horizontal overflow. All
sixteen sparklines draw, stroked `stroke-series-1` or `stroke-critical-solid`.

**One defect found and fixed.** The trend cell was `w-26` — 104px, exactly the sparkline — while also
carrying a 12px gutter, so the drawing pushed into the row's own right padding and sat **4px from the
edge where every other column sat 16px in**. Now `w-29`; measured at 16px, matching its neighbours.
No test could have caught this: jsdom performs no layout.

**Two design findings, both since resolved and re-verified live.** The sparkline now plots the series
its colour claims, and the flat lines stay. See Questions.

### T7–T34 — the archetype, the filters, the investigation core, the surfaces

Recorded in their commits rather than here at the time, and summarised now so this document is not
missing the middle of its own phase. The three findings worth carrying forward:

- **Server-side filtering was the change that mattered**, not the filter bar. Narrowing a list in the
  browser narrows a page of a capped read, so a screen with a cap and a client-side filter answers
  confidently with the wrong rows. Seven screens now push their narrowing into SQL, and a live test
  per screen proves the filter reaches past the cap.
- **A project's p95 is not the average of its services' p95s.** It is a `quantilesMerge` across their
  states, and on real data the two answered 21.8ms and 3,455ms — a factor of 159. `/services/overview`
  exists only because of that measurement.
- **Eighteen host metrics were collected, masked, retained and read by nothing.** Saturation was never
  missing from the product; it was missing from the reader. T34 grew from a labelling fix into the
  full USE frame on the founder's call.

### T35–T42 — metrics, the guard, and the feed

| Planned | Actual |
|---|---|
| T36 merges histogram buckets across the window | **A window *delta*, not a merge.** Every histogram in the store is CUMULATIVE, exported every ten seconds, so summing points reports the traffic hundreds of times over. The plan did not account for temporality, and nothing about the wrong answer would have looked wrong |
| T36 refuses to merge points whose bounds differ | Unchanged, and **the case is real** — `db.client.operation.duration` arrives in milliseconds from one service and seconds from another. The live test asserts against that rather than a constructed pair |
| T37 draws a histogram over its own bounds | Also needed **column headings and axis labels**, both found in the browser. Without boundary labels the chart is a row of heights, not a distribution |
| T38 guards three screens | **Six forms across three screens.** Project settings is three independent forms with three drafts; one guard per screen would warn about the wrong one |
| T40 puts the auth screens on the Gateway archetype | **Already done.** Established by reading all six screens |
| T39 makes onboarding end in a confirmation | **Half already done.** The confirmation existed; the two onward actions the wireframe commits did not |
| T41 builds the Findings feed | Built — and it was the last placeholder route, so `placeholder.tsx`, `ComingSoon.tsx` and the manifest's screen tables were deleted with it |

#### What the review found

No tenancy, authorization, durability or masking finding. The new ClickHouse statements are
module-level constants carrying the tenant predicate as a literal, and the structural test parses
them. Both new routes are reads behind `require_project`. The new web code imports nothing from
another feature. Every absent reading renders as an em dash rather than a zero, including the
estimates and the exporter's optional `sum`, `min` and `max`.

Two things were wrong and are fixed: a structural test added in T38 used `node:fs` in a package that
compiles without node types — `status-interpretation.test.ts` had already established Vite's glob as
the way to read sources here, and I did not re-run the typecheck after adding it. And the metrics
screen was first written against four design tokens that do not exist (`caution-*` rather than
`warning-*`); Tailwind class names are strings, so nothing failed — it would simply have rendered
unstyled.

#### Design QA — T35–T42, against the running app

Three defects found in the browser, none reachable by a test. The metrics catalogue had four
unlabelled numeric columns. The histogram had no boundary labels. And the five percentile estimates
were laid out as a grid spanning the chart's width, which put them at 0, 25, 50, 75 and 100% across
the bars and read as **marks on the axis** — a position they do not have. All three fixed and
re-verified, with the axis labels checked against the stored bounds rather than by eye.

Six further routes were opened and swept for console errors after the placeholder system was deleted.
Clean. That is not the same as a design QA of every rebuilt screen, which T44 still does not have.

#### Left for the founder

- ~~Two duplicate-weighted `avg()` sparkline means~~ — **fixed 2026-09-13.** The note said 22%; the
  measured error is about 8%, and only while a redelivery overlaps. Recorded because the number in
  the note was wrong, not only because the defect is gone.
- ~~Whether the telemetry tier is sized for a dashboard~~ — **answered 2026-09-13.** It was not: the
  cap sat at four while service detail asked for five, which only became visible once two ClickHouse
  routers were moved onto the tier that was supposed to hold them. Now eight, tied by a test to the
  largest screen's fan-out.
- ~~The conditional duration heatmap (OD7)~~ — **built 2026-09-13.**
- **A warning tier for services**, still undecided, still blocking the shape distinction.
- **A "has this project ever reported" signal**, which would replace a heuristic.
- **A bounded wait instead of a refusal on the telemetry tier.** Raising the cap fixes today's
  screens; it does not change that a legitimate burst one over the cap is answered with a 429 rather
  than a queue. The next screen to grow past eight meets the same wall.
- **The insight engine.** Findings is built and feeds on nothing. Until it exists the healthy state is
  the whole screen, which is why Overview holds `/`.
