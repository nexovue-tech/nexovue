# Status: console-redesign

## Lifecycle status

Approved

## Current task

**Awaiting approval of `design.md`, the five wireframe batches, and `plan.md`.** Nothing is
implemented and nothing begins until all three gates clear. Twenty of twenty screens are covered,
except the two blocked on OD1 and OD2. `design.md` still awaits
approval alongside it.

OD1 and OD2 remain open. They do not block the layout system, which is common to every screen, but
they do block two specific wireframes: the Findings feed and the dashboard surface.

## Completed

- Data inventory established from the ClickHouse schema, the rollup tables and the API read surface,
  rather than from the existing screens.
- Screen inventory taken from `apps/web/src/app/routes/route-manifest.ts`: 20 routes.
- `spec.md` written and **approved 2026-09-12**.
- `user-flows.md` written: three personas traced against screens that exist. Found that P3 has no
  console surface, that `deploy_events` is served with no screen consuming it, and that the Dev's
  core loop is four hops with context lost at each.
- `prototype-ranked-entities.html` built and published: the row rendered from the real tokens,
  because a markdown wireframe cannot answer whether the density is right. Founder reviewed it.
- OD2 resolved 2026-09-13: auto-composed RED/USE dashboards, no builder; Overview is its own entry
  and holds `/` until the insight engine exists. `wireframes-dashboard.md` written; `spec.md` and
  `design.md` amended to match rather than left to disagree.
- Wireframes written for every screen: `wireframes-ranked-entities.md` (batch 1),
  `wireframes-investigation.md` (batch 2), `wireframes-surfaces.md` (batch 3). Every field was taken
  from the API's own response schemas, which is what turned three assumptions into corrections.
- Founder decided 2026-09-12: widen `ServiceHealth` with p95, and absolute-impact ranking stands.
- `plan.md` written: seven phases, sequenced so phase 1 is one screen end to end. `docs/design/principles.md`
  rule 3 extended from six states to the full set, split into the states a screen owns and the six it
  must never reimplement because `app/query-client.ts` owns them.
- Founder decided 2026-09-13: a stale infrastructure node is **critical**. Implementation note: the
  threshold is measured against `NodeSummary.reporting_interval_seconds`, the node's own cadence, so
  an autoscaling fleet does not render as a wall of red.
- `design.md` written: the layout system — nine archetypes covering twenty routes, the
  ranked-entity row anatomy, the `Sparkline` contract, a three-step density scale, the shell change,
  and states defined per archetype rather than per screen.

## The scope problem, stated plainly

The feature grew four times, each time on a founder question that was right to ask. It is now 21
routes, 21 acceptance criteria, backend work on nearly every read endpoint, one new screen and ten
unbuilt components. As one branch it would be months long and would prove the archetype only at the
end, where every wrong assumption surfaces together.

`plan.md` answers that with [ADR-0019](../../decisions/decision-log.md)'s own rule — walking
skeleton, then widen. **Phase 1 is the Services screen alone, end to end, and exists to be wrong
cheaply.** Its recommended stop point is after phase 3: five screens rebuilt, filterable and
carrying context is a usable product, and the rest can be re-judged then rather than committed to
now.

## Next action

Founder reviews `design.md` and batch 1. Three questions in the wireframes need answers before
implementation: batch 3's AC4 correction below. Batch 2 asks three: exposing latency quantiles across all
three schemas at once, whether span `events` and `links` are in scope, and where a deploy list lives
once markers exist.

**Correction, 2026-09-13.** Batches 1 and 2 reported that no list endpoint returns latency and that
the prototype's p95 column was unservable. Both were wrong, and the error propagated into AC16, the
plan's first two tasks and several commit messages. `ServiceHealth`, `EndpointHealth`, `ServiceEdge`
and `TrendPoint` all already carry `p50_ms`/`p95_ms`/`p99_ms`, merged by `quantilesMerge` in
`queries.py`. The cause was a grep pattern that skipped fields declared after a comment line; the
lesson is to read a schema rather than pattern-match it.

`GET /services/trend` with no service named also already returns one `ServiceTrend` per service in a
single response — the batched series read the plan listed as work. **Phase 1's first two tasks are
already done**, and what remains genuinely missing for the archetype is the `Sparkline` component and
the screens.

The corrections are recorded in the affected documents rather than deleted.

After batch 1, batches 2 and 3 cover the remaining archetypes.

The layout-system decisions worth the most scrutiny are the nine
archetypes, the ranked-entity row, and the rule that a sparkline shares one window across a screen
rather than auto-scaling per row.

On approval, per-screen wireframes begin in batches of three or four. The first batch would be the
Ranked entities archetype, which covers five screens and is the highest-leverage part of the work.

**OD1 and OD2 still block two wireframes** — the Findings feed, and the dashboard surface and its
place in the sidebar. Neither blocks the layout system or the first batch.

## Everything open is now decided

As of 2026-09-13 there are no open decisions in this feature. The four that remained were accepted
together:

- **OD1** — the Findings feed is wireframed with its structure committed and its card contents marked
  provisional, so the drawing does not become the insight engine's specification by default.
- **OD7** — the duration heatmap is conditional, drawn only for customers sending OTLP histograms.
  The rejected alternative was a pre-sampling `duration_histogram_1m` rollup, which would have been
  the only schema change in this redesign and would have put a new write in the most
  correctness-critical path in the system. **Most customers will not see this panel.**
- **Latency quantiles** exposed once across `ServiceHealth`, `EndpointHealth` and `ServiceEdge`.
- **Span `events` and `links`** in scope on trace detail.

`spec.md` gains AC16–AC18. The feature now carries 18 acceptance criteria over 21 routes.

## Open decisions raised by the gap analysis

`data-and-filter-gaps.md` asks the inverse question the wireframes asked: not "what do we store and
fail to draw" but "what would a customer want, and can the product answer it at all?" Two findings
change the shape of this feature.

**OD8 — ACCEPTED 2026-09-13, in scope.** No number on any screen has context. There is no period-over-period comparison anywhere in
the API; every apparent match for *compare* or *baseline* is `hmac.compare_digest` or a comment. A
customer cannot tell whether 4.1% errors is bad, which is the knowledge a monitoring product exists
to supply. One extra read against rollups that already exist would answer "worse than yesterday" for
every screen. Now in scope.

**OD9 — ACCEPTED 2026-09-13, in scope.** Seven screens have no filters at all. Traces accepts seven, logs five; service map,
synthetics, database, infrastructure, RUM and usage accept none beyond time and limit. Every filter
they lack is over a column already stored and already in a sort key, so this is pure API work.
Now in scope, and the cheapest work in the feature.

`spec.md` gains AC13–AC15 and `design.md` gains two sections: how a delta is shown, and one filter
contract per archetype. The three wireframe batches name the concrete filters and columns per screen
rather than being redrawn.

Category C gaps — service ownership, a session dimension on spans, cross-project comparison, saved
views, export, alerting — are named in the analysis and recommended **out** of this feature. Folding
them in turns a console redesign into a platform redesign that never ships.

## Decision record — heatmap (was OD7)

**OD7 — the duration heatmap.** It cannot be drawn from what is stored. `endpoint_stats_1m` keeps
`AggregateFunction(quantiles(0.5, 0.95, 0.99))` — three points, not a distribution — and
`spans.duration_ms` is post-sampling, so its shape would be wrong. Only `metrics_histogram` carries
`bucket_counts` and `explicit_bounds`, and only when a customer sends OTLP histograms.

Three options: draw it conditionally for those customers; add a pre-sampling `duration_histogram_1m`
rollup, which is **the only schema change this redesign would require**; or drop the panel.
Recommendation is the first, with the second only if customers ask — adding a write to the
pre-sampling path trades correctness risk in the most load-bearing part of the system for a panel
nobody has requested.

## Specification impact

Both amendments below are now applied to `spec.md`, marked with their date.

**AC4 amended from twelve dimensions to eleven.** `web_vitals.country` is a promoted column
with no producer — `BreakdownDimension`'s docstring states there is no GeoIP anywhere in the system,
so a `GEO` member would name a question the store cannot answer. The column cannot be shown,
filtered, or deliberately hidden for a design reason; it is a schema question. Drop the column or add
GeoIP, both founder decisions.

**Scope widened from 20 routes to 21**, and `design.md` from nine archetypes to ten. The chart count
goes from 6 to roughly 20 — the deficit the founder identified was OD2 left unanswered, not a
decision to keep the console sparse.

`apps/web/src/app/routes/route-manifest.ts` carries a comment saying "a dashboard must never become
the front door". That records the superseded stance and must be updated in implementation, together
with `route-manifest.test.ts`, which enforces router/sidebar agreement.

## Blocked

Not blocked. Awaiting the approval gate.

## Validation

Nothing to validate by test; no code has been produced.

The design's factual claims were taken from the code: the token names it uses exist in
`packages/ui/tokens.css`; the shell behaviours it leaves unchanged are those built in
`apps/web/src/app/layout/AppShell.tsx`; the sidebar's three current groups and the twenty routes
come from `route-manifest.ts`; and `/services/trend` already returning a bucketed series for one
service is what makes the batched-series read a known risk rather than a guess.

The specification's factual claims were taken from the code, not from documentation:

- Chart coverage — two of sixteen feature folders reference uPlot (`servicedetail`, `synthetics`).
- `Sparkline` appears nowhere in `apps/web` or `packages/ui`, while `docs/design/design-system.md`
  names it as the form a trend takes.
- `ServiceTrendResponse` / `TrendPoint` already serve bucketed series with a server-chosen bucket
  size, so most screens need no new backend read to show a trend.
- The rollups carry per-minute counts and quantile state and are written before sampling.
- The 20 routes and the three sidebar groups come from `route-manifest.ts`.

## Documentation impact

Three places record "insight-first, not dashboard-first" and contradict the founder's 2026-09-12
decision that both are first-class. They are listed in the specification's Dependencies section and
must be updated as part of this feature: `docs/product/vision.md`,
`docs/product/feature-catalog.md` (F20), and `apps/web/src/app/routes/route-manifest.ts` — the last
of which is enforced by `route-manifest.test.ts`.

## Last updated

2026-09-12
