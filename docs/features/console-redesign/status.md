# Status: console-redesign

## Lifecycle status

Approved

## Current task

Batches 1 and 2 of the wireframes await founder review. `design.md` still awaits
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
- `wireframes-ranked-entities.md` (batch 1) and `wireframes-investigation.md` (batch 2) written:
  ten of the twenty screens, every field taken from the API's own response schemas.
- Founder decided 2026-09-12: widen `ServiceHealth` with p95, and absolute-impact ranking stands.
- `design.md` written: the layout system — nine archetypes covering twenty routes, the
  ranked-entity row anatomy, the `Sparkline` contract, a three-step density scale, the shell change,
  and states defined per archetype rather than per screen.

## Next action

Founder reviews `design.md` and batch 1. Three questions in the wireframes need answers before
implementation: **whether a stale infrastructure node is a warning or a critical** — still unanswered, and the
only batch-1 question outstanding. Batch 2 adds three more: exposing latency quantiles across all
three schemas at once, whether span `events` and `links` are in scope, and where a deploy list lives
once markers exist.

**Batch 2 turned the p95 gap into a systemic finding.** `ServiceEdge` carries no latency either,
while `service_edges_1m` stores the same `latency_quantiles(0.5, 0.95, 0.99)` as
`endpoint_stats_1m`. Quantiles are computed pre-sampling into every rollup and exposed by no list
endpoint — one pattern to fix once, not three screens to patch.

**Batch 1 confirmed a risk as fact: none of the five list endpoints returns a time series, and
`ServiceHealth` carries no latency at all.** The archetype needs backend work on every screen. The
p95 column in the prototype is currently unservable.

After batch 1, batches 2 and 3 cover the remaining archetypes.

The layout-system decisions worth the most scrutiny are the nine
archetypes, the ranked-entity row, and the rule that a sparkline shares one window across a screen
rather than auto-scaling per row.

On approval, per-screen wireframes begin in batches of three or four. The first batch would be the
Ranked entities archetype, which covers five screens and is the highest-leverage part of the work.

**OD1 and OD2 still block two wireframes** — the Findings feed, and the dashboard surface and its
place in the sidebar. Neither blocks the layout system or the first batch.

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
