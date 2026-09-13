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

- [ ] **T39** `P` — `features/onboarding/OnboardingScreen.tsx` ends in a confirmation naming the
      service that arrived (IA4), not the trace list.
- [ ] **T40** `P` — `features/auth/`: login, signup, recovery on the Gateway archetype.
- [ ] **T41** `P` — `features/findings/` (new): feed structure, ordering, acknowledge/resolve; the
      card provisional.
- [ ] **T42** `P` — the healthy state carrying the RED summary, and **visibly distinct from
      failed-to-load** (AC18). → a test asserting the two differ.

### Closing

- [ ] **T43** — `/review-diff`.
- [ ] **T44** — Design QA: every rebuilt screen against its wireframe and the prototype. *Nothing
      implements this gate today.*
- [x] **T45** — `docs/architecture/frontend.md` updated to current truth. It claimed a database
      *screen* that is now a view inside infrastructure, and predated `Sparkline`, `FilterBar`,
      `Delta` and the rule that narrowing a list is the server's job.
- [x] **T46** — the superseded stance, in **five** places rather than the three the audit found:
      `vision.md`'s one-line vision and its Decisions Log, `feature-catalog.md` F20 *and* its own copy
      of the vision line, and the comment in `route-manifest.ts`. The Decisions Log keeps the 2026-08
      row and marks it superseded rather than rewriting it — a decisions log that edits its own
      history cannot be used to find out why something changed.
- [ ] **T47** — **Outcome** filled in; `status.md` final.

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

**T1–T6 complete, 2026-09-13.** `just check` exit 0: 2277 Python, 120 browser-SDK, **630 web across
48 files** — up from 613/47, so 17 tests were added.

### What changed from this plan

| Planned | Actual |
|---|---|
| Four files | **Six** — `Sparkline.tsx` and `Sparkline.test.ts` were counted as one line in Impact |
| T4 draws critical *and* warning, distinguished by shape | **Critical only.** No error-rate threshold for a "warning" service exists anywhere in the code, and inventing one would have decided product behavior in a component. The mark is binary, exactly as the errors cell beside it already was. Recorded as a question below |
| T5 splits on whether data ever arrived | **Splits on whether the widest range is already selected.** No API says whether a project has ever received telemetry. The proxy is wrong in one direction only — a project silent for over seven days reads as never having reported — which costs a reader a sentence, where the opposite error would send a customer with working instrumentation back to setup |

### What the work found

- **`Overrides` in `ServicesView.test.ts` had no `windowLabel`,** and vitest does not typecheck, so
  the tests passed while the build failed. `lint-web`'s own comment predicted exactly this: *"biome
  does not typecheck, so without this a type error passes every gate."*
- **One pre-existing test asserted the superseded empty-state copy** and was updated rather than
  deleted — its intent still holds, only the wording moved.
- **jsdom does no layout**, so no test could have caught an invalid Tailwind class. The four new
  utilities were verified against the built CSS instead: `size-1.75` resolves to 7px, matching the
  prototype exactly.

### Left for the founder

- **A warning tier for services.** Today a service is failing or it is not. Whether an error rate
  below some threshold should read as warning rather than critical is undecided, and the shape
  distinction in `design-system.md` waits on it.
- **A "has this project ever reported" signal.** It would replace the heuristic in `EmptyServices`
  with a fact.

### Design QA — done 2026-09-13, against the running app

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
