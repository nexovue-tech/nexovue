# Status: dashboard chart scale (F20 open decision 39)

## Lifecycle status

**Implemented, verified on this machine's own stack. Uncommitted.**

## Current task

None. Waiting for the coordinator to commit.

## Completed

- Three faults under one symptom found and fixed at their roots; see `bug.md`.
- Files: `TrendChart.tsx`, `trend-chart-options.ts` (+ its snapshot), `PanelFrame.tsx`, `DashboardScreen.tsx`,
  `TrendChart.test.ts` (new), `tools/e2e/tests/dashboards-chart.spec.ts` (one stale assertion).
- Nothing deleted: the change replaces one call's argument and adds a range function and a prop.

## Next action

Coordinator: commit. Founder: decision 1 below. Then F20's decision 39 can close.

## Open decisions

1. **The span around a lone reading** is `ONE_READING_SPAN_SECONDS` = 1 hour, centred (a guess, marked in
   the code). A reading in a 6 h window is a 6-minute bucket, so a drag across it can ask for a window much
   narrower than the page's bucket. Option: pass the bucket size to the chart and draw one bucket either side.
   Recommendation: keep the hour until the demo gate; the bucket is the better answer and needs `TrendChart`
   to take it.
2. **The placeholder holds the last height, not the right one.** First load still has no height to hold, so
   the page still grows as readings arrive. Option: size each placeholder from the panel's kind and line
   count (a latency chart's three legend rows are known from its settings; a top-N's count is not).
   Recommendation: keep.

## Review (`/review-diff`)

Every changed file is explained by `bug.md`. No tenancy, authorization, migration or secrets surface is
touched. Findings:

- MEDIUM, recorded: the one-reading range is in `chartOptions`, so **every** chart built from it (Overview,
  service detail, releases, findings, SLOs, profiles, as well as dashboards) now draws a lone reading in an
  hour, not 2.7 years. That is the intended correction, but it widens the change past dashboards; the
  options snapshot (AC43's "other screens unchanged") was updated for it, two lines.
- MEDIUM, recorded: `holdHeight` has no unit test, since jsdom has no layout; the Playwright spec holds it.
- LOW: `PanelFrame` reads a ref while rendering (`readingHeight.current`). It is only the last measured
  height and a stale value costs a few pixels, so a state variable and its extra render were not added.

## Blocked

Nothing.

## Risks

- The 2.7-year behaviour is uPlot 1.6.32's; whether a later version changes it was not checked. The range
  function would then be redundant but harmless.
- `holdHeight` keeps a frame tall while its panel's answer is missing, including a panel whose answer the
  server drops for good; the row stays as tall as it was until the next reading.
- Not run: a phone, Safari, and Firefox; the founder's hand checks stay open.

## Validation

All on this machine's own stack (Postgres 18, ClickHouse 26.3, Kafka; receiver, worker, API, Vite from
source; Playwright 1.64.1 on Chromium 1243 through a browsers-path link outside the repository).

- **Before:** `dashboards-chart` and `dashboards-link`: 2 failed, 3 passed.
- **After faults 1 and 2:** the chart spec's cursor assertion passed and AC103 passed 5 of 5; the chart spec
  then failed on one drag reading twice (expected 2, received 3), 5 of 5.
- **After fault 3 and the spec line:** `dashboards-chart` and `dashboards-link` together, 5 runs in a row,
  each **5 passed** (4 tests and the stack check).
- **All `dashboards-*.spec.ts`:** 17 passed (16 tests and the stack check), 1.8 m.
- **Web vitest, whole package:** 2,688 passed, 1 failed (2,689): `build/setup-docs-plugin.test.ts`, "leaves no
  link pointing at a file", for the `dashboards` setup page. It fails the same on a clean checkout of this
  commit with my change stashed, so it is not this change's; it belongs to F20 Step 11's setup page.
- **New unit file:** `TrendChart.test.ts`, 3 passed; `shared/components`, 146 passed after the snapshot
  update.
- **`pnpm -r --if-present run typecheck`:** `packages/browser-sdk`, `tools/e2e` and `apps/web` done, no
  errors. **Biome** on the six touched files: clean. (Biome over all of `apps/web/src` and `tools/e2e` has two
  existing warnings, `connection-health.ts` and `rum.spec.ts`, neither touched.)
- **Not run:** `just check`.
