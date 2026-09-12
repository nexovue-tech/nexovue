# Task plan: console-redesign

> **Status:** Awaiting approval. Produced by `task-plan` on 2026-09-13. No file edited yet.
>
> Covers the thin path in [`plan.md`](plan.md) — `/services` on the ranked-entity row, end to end.
> The later steps are not expanded here: expanding them before the first screen is built would be
> fiction with checkboxes, and this document is re-expanded as the work moves on.

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

| Area | What changes |
|---|---|
| Files | `shared/components/Sparkline.tsx` (new) · `features/services/api.ts` · `features/services/ServicesScreen.tsx` · `features/services/service-rows.ts` |
| Services | none |
| Schema / migrations | none |
| UI | `/services` only |
| Tests | `Sparkline.test.ts` (new) · `ServicesView.test.ts` · `service-rows.test.ts` |

**No backend change.** Both API steps the plan originally listed turned out to be built.

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

- [ ] **T1** — `shared/components/Sparkline.tsx`. Inline SVG, no axes or legend, single stroke plus
      an emphasized endpoint. Takes an explicit `max` so the scale is the caller's, never per-row.
      Below a minimum bucket count renders `UNKNOWN` rather than a line.
      Proved by `Sparkline.test.ts`: identical series at different `max` render different heights;
      too-few-buckets renders the em dash; an empty series does not throw.
- [ ] **T2** — `features/services/api.ts`: `useServiceTrend(projectId, timeWindow)` calling
      `services/trend`, returning a `ReadonlyMap<string, readonly TrendPoint[]>` so a row looks up by
      name. One request for the whole list.
      Proved by extending `ServicesView.test.ts` with a fixture map.
- [ ] **T3** — `ServicesScreen.tsx`: pass `trendByService` into `ServicesView`, add the trend column
      to `MetricCells`, and compute one screen-wide `max` across every service's series.
      Proved by `ServicesView.test.ts`: two services with different magnitudes render against the
      same scale; a service absent from the map renders the em dash and still renders its row.
- [ ] **T4** — the severity mark. A row with errors carries a mark; a healthy row carries none.
      Critical and warning differ by **shape as well as hue** (`design-system.md`).
      Proved by `ServicesView.test.ts`: a healthy row has no mark.
- [ ] **T5** — split the empty state. "Nothing has ever arrived" offers setup; "nothing in this
      window" offers a wider range. Today's copy is the second only.
      Proved by `ServicesView.test.ts`, one case each.
- [ ] **T6** — a test fixing the ranking: 50% of 96 requests ranks below 4.1% of 1,204. The order is
      the API's and `service-rows.ts` must not resort it under `worst`.
      Proved by `service-rows.test.ts`.

## New risks

**The screen-wide `max` is computed from the loaded page, not the project.** With a `limit` of 100
services the scale is correct for what is shown, but adding a 101st taller service would rescale
every sparkline. Acceptable here and worth remembering when filters arrive, which change what is
loaded.

**`useServiceTrend` doubles the screen's requests** — list and trend, in parallel. Both are bounded
and cached on the same key shape, but a slow trend must not block the list from rendering: the rows
appear with em dashes and fill in.

## Questions

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| Q1 | `formatErrors` (`shared/format/health.ts`) returns the em dash when `error_count === 0` — so "no errors" and "unknown errors" render identically. `principles.md` rule 2 says a real zero and an unknown must never look alike. One of the two is wrong. | **Yes** — T4 and T5 both touch this cell | `docs/design/principles.md`, or `health.ts` |
| Q2 | Does the trend sparkline show **rate** or **error rate**? The wireframe's row says rate; the design says a critical row's sparkline is stroked with the critical colour, which only reads if it is the error series. | No — assume rate, stroke critical on a failing row | [`design.md`](design.md) §6 |

**Q1 is worth a moment.** The code's reasoning is written in `health.ts`: *"a service that failed
nothing gets a rule, not a zero to read past."* That is a deliberate choice about noise, and it
predates the principle. Either the principle is too absolute, or this cell is an exception that
should be named in it. Both are defensible; it is not mine to pick.

## Validation

`just check`, output and counts read. New tests are Vitest, colocated.

Phone width at 375px checked on the rebuilt screen — the sparkline column is the first thing that
would overflow.

The built screen compared against [`prototype-ranked-entities.html`](prototype-ranked-entities.html).
**Nothing performs this check today** — `plan.md` names a Design QA gate and no skill implements it,
so this is a manual look.

## Outcome

_Filled in when the work completes._
