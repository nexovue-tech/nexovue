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

### Make the screens answerable — filters and context

- [ ] **T15** `P` — `shared/components/FilterBar.tsx` and `TimeRangePicker.tsx` (new).
- [ ] **T16** `P` — filters on `servicemap`, `synthetics`, `dbstats`, `infrastructure`, `rum`, `usage`
      — API query params over columns already stored. → one API test each.
- [ ] **T17** `P` — `rum`: `device_class` and `route` become filters, not only breakdown dimensions.
- [ ] **T18** `P` — filtered-empty vs never-had-data on every screen that gained a filter (AC15).
- [ ] **T19** `P` — previous-period delta: a second read over the shifted window, one threshold per
      measure, rendered **only where significant** (AC13, AC14). → tests that an unremarkable reading
      carries no annotation.
- [ ] **T20** `P` — a structural test that no feature interprets an HTTP status itself (AC21).

**A reasonable stopping point. Five screens rebuilt, filterable, carrying context.**

### Investigation core — five screens

- [ ] **T21** `P` — `traces/schemas.py`: `SpanDetail` exposes `exception_type`, `exception_message`,
      `deployment_version`; `TraceSummary` exposes `span_services`, `span_endpoints`,
      `worst_severity`. → API tests.
- [ ] **T22** `P` — span `events` as a timeline and `links` as navigation in
      `features/traces/SpanDetail.tsx` (AC17).
- [ ] **T23** `P` — `features/logs/LogsScreen.tsx`: facets as counts; the trace arrow only when
      `trace_id` is non-empty; Live as a toggle sharing the search filters.
- [ ] **T24** `P` — `features/traces/TraceListScreen.tsx`: duration bar on a window-wide scale;
      selection arrives pre-applied.
- [ ] **T25** `P` — `features/traces/TraceScreen.tsx` + `TraceWaterfall.tsx`: detail pane subordinate
      to the waterfall; `TraceLink.exists: false` as its own state, not a load failure.
- [ ] **T26** `P` — `features/servicedetail/`: one real chart, deploy markers (IA3), endpoints on the
      row.
- [ ] **T27** `P` — `features/servicemap/`: volume floor and failing-only; `callee_kind` labels the
      node; `edge_kind` distinguishes a queue edge from a call.
- [ ] **T28** `P` — selection travels (IA1): a shared scope in `shared/selection.ts`, applied on
      arrival rather than re-chosen. → `selection.test.ts`.

### The dashboard surface

- [ ] **T29** `P` — `features/overview/` (new) + route at `/`; three RED panels from
      `endpoint_stats_1m`; ranked-entity row reused.
- [ ] **T30** `P` — one deploy marker spanning all three panels; `deploys` API consumed for the first
      time.
- [ ] **T31** `P` — `route-manifest.ts`: Overview above Signals, Findings its own entry; **the "never
      the front door" comment records a superseded decision** — update it and
      `route-manifest.test.ts` together.
- [ ] **T32** `P` — service detail expanded to the full RED set: distribution, operations,
      dependencies from `service_edges_1m`.
- [ ] **T33** `P` — infrastructure USE panels, honest about having no saturation measure.

### Surfaces and account — six screens

- [ ] **T34** `P` — `features/rum/RumScreen.tsx`: `readings` as a column, `is_route_template`
      labelled, ratings named in words not only colour.
- [ ] **T35** `P` — `infrastructure/signals` widened past host metrics into a metric catalogue.
- [ ] **T36** `P` — `features/metrics/` (new): the catalogue plus a histogram reader over
      `bucket_counts`/`explicit_bounds` (AC5); the heatmap **only** where a customer sends OTLP
      histograms.
- [ ] **T37** `P` — `features/usage/UsageScreen.tsx`: `settled: false` rendered, `services` beside
      `billed_services`.
- [ ] **T38** `P` — settings ×4 — `ApiKeysScreen`, `ProjectsScreen`, `BillingScreen`, `ProfileScreen`
      — where the dangerous state does most of the work.
- [ ] **T39** `P` — unsaved-changes warning on every editable form (AC19).
      → one test per form.

### Gateway and the Findings shell

- [ ] **T40** `P` — `features/onboarding/OnboardingScreen.tsx` ends in a confirmation naming the
      service that arrived (IA4), not the trace list.
- [ ] **T41** `P` — `features/auth/`: login, signup, recovery on the Gateway archetype.
- [ ] **T42** `P` — `features/findings/` (new): feed structure, ordering, acknowledge/resolve; the
      card provisional.
- [ ] **T43** `P` — the healthy state carrying the RED summary, and **visibly distinct from
      failed-to-load** (AC18). → a test asserting the two differ.

### Closing

- [ ] **T44** — `/review-diff`.
- [ ] **T45** — Design QA: every rebuilt screen against its wireframe and the prototype. *Nothing
      implements this gate today.*
- [ ] **T46** — `docs/architecture/frontend.md` updated to current truth.
- [ ] **T47** — `docs/product/vision.md` and `feature-catalog.md` F20: both still record
      "insight-first, not dashboard-first", superseded 2026-09-12.
- [ ] **T48** — **Outcome** filled in; `status.md` final.

## New risks

**The screen-wide `max` is computed from the loaded page, not the project.** With a `limit` of 100
services the scale is correct for what is shown, but adding a 101st taller service would rescale
every sparkline. Acceptable here and worth remembering when filters arrive, which change what is
loaded.

**Forty-eight tasks is the real size of this feature**, and only six are exact. That ratio is the
honest picture: the thin path is known, and everything after it depends on whether the archetype
survives. The two tasks with no code behind them at all — T45's Design QA gate, and T36's Metrics
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
