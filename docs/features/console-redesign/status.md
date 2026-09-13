# Status: console-redesign

## Open decisions from the follow-up phase (2026-09-14)

**D1 — Cancellation timing. Money-facing, and the code and the screen disagree today.**
`razorpay.cancel` sends `cancel_at_cycle_end: 0`, which ends the mandate immediately.
`billing-view.describeCancellation` tells the customer the plan "returns to the free tier at the
end of the period the processor is billing". Both cannot be true.

The reasoning originally written in the adapter was **wrong**: it said cancelling at a cycle
boundary "would keep charging somebody who cancelled". Verified against the vendor's
cancel-subscription documentation on 2026-09-14 — `cancel_at_cycle_end: 1` ends the subscription
when the current cycle completes, which stops every future charge and lets the period already paid
for run out. The docstring is corrected; the behaviour is not, because picking one silently is how
a customer ends up told one thing and billed another.

- `0`, as now: access ends immediately, for a period the customer has already paid for. The copy
  must change.
- `1`: future charges stop and the paid period finishes. The copy is already right, and this is
  what most subscription products do.

**Recommended: `1`.** It matches what the screen already promises and what a customer expects, and
the only argument recorded against it was a misreading of the API. Nobody is affected yet —
nexovue is pre-deployment.

**D2 — Store the charged amount.** There is no payment or invoice table: `billing_webhook_deliveries`
records *that* a charge succeeded and *when*, never *how much*, deliberately. The charge history
therefore derives the amount as `rate × services_committed`, which is exact except for a checkout
spanning midnight UTC and is always exclusive of provider tax. The durable fix is a `Money` on the
billing event and a column to persist it — `models.py` and a migration. Storing the rate on
`billing_subscriptions` would close the first hole on its own.

**D3 — How a non-JSON response is addressed.** `docs/engineering/api.md` says nothing about them and
there were none. The charge export is `GET /billing/history.csv` with `Content-Disposition`.
`?format=csv` or `/export` are equally defensible; it is one line either way, and whichever is
chosen should be written into `api.md` so the next one matches.

**D4 — The OTLP endpoint is unrecoverable from the console.** It is on no screen: not the projects
list, not API keys, not the key-create response. A customer who loses the signup handoff cannot get
it back. Small to fix, and nothing today tells them where to look.

**D5 — `MAX_CORRELATED_DEPLOYS = 1_000`** is an engineering bound chosen while building T65, not a
decision anybody took.

**D6 — The browser SDK is unobtainable.** `@nexovue/browser-sdk` is `"private": true` with no
registry and no CDN, so RUM is documented and cannot be installed by a customer.

**D7 — Error-rate thresholds (Q6).** T61 is built on 1% warning / 5% critical, stated on screen, as
the recommendation the approved plan recorded. One named constant if you want different numbers.

## Lifecycle status

**Implemented, 2026-09-14.** Both phases are built. Not deployed: nexovue has no deployment, so
"implemented" is as far as a feature goes here. Seven decisions are open and listed above; none of
them blocks the code.

## Current task

**None — the follow-up phase is complete.** `just check` exit 0: 2709 Python (1 skipped), 903 web
across 70 files, 120 browser-SDK. The task plan's Outcome carries what each task planned against
what it cost, the five defects the work turned up that no task named, and three of my own mistakes
worth keeping.

### What it was, before it was built A pass over every screen, read
as a customer rather than as the person who built it, found that the console is correct and does not
yet behave like a product somebody uses all day: every refresh is a cold reload rather than a
background fetch, no window can be absolute so paid retention is unreachable, nothing pauses, the
selection does not survive a reload, ⌘K shows a customer an internal task id, and the metrics screen
has no filters. None of it is a defect in what was built; all of it is what the building did not
cover.

Two of the four decisions that bound the list are exclusions: **team membership becomes its own
feature** rather than part of this, and **the insight engine is the next feature**, so Findings keeps
its healthy state meanwhile.

Nothing below has been expanded from `P` to `E`, and no file has been edited for it. That is the
gate.

### The first phase, completed 2026-09-13

`just check` exit 0 at close: 2438 Python (1 skipped, the
opt-in billable mail suite), 805 web across 64 files, 120 browser-SDK — 2469 and 813 after the loose
ends were closed on 2026-09-13. The task plan's Outcome carries the full record of what each block
planned against what it cost.

**Every item on `plan.md` is now built**, including the conditional duration heatmap (OD7) that was
carried open rather than quietly ticked.

Closing it found two defects nothing else would have: `TELEMETRY_PATHS` named `/errors` and
`/database` while the routers are mounted at `/error-groups` and `/dbstats`, so two ClickHouse
readers had run on the default tier with no concurrency cap and nothing reported it; and fixing that
exposed a cap of four sitting under a screen that asks for five.

---

### The record below is the phase's history, oldest first

**T1–T6 complete** — the thin path. `/services` now carries the ranked-entity row: `Sparkline`, the
shared-scale trend column, the severity mark, and two empty states instead of one. 17 tests added,
`just check` green.

**T1–T21 are built and `just check` is green** (2342 Python, 684 web, 120 SDK). The filter audit was parsed from every
router's AST rather than grepped, after six greps in this feature returned wrong answers.

It found one thing that changes the work: **three filters exist only in the browser** — infrastructure's
kind filter, services' `slowest` ordering, and errors' new-in-window — and each sits under a server
cap. A client-side filter over a capped page answers about the page, not the data, and nothing on
screen can say so. T18 moves them to the server.

**T7–T13 complete.** Errors, synthetics and the merged Infrastructure/Databases destination all
carry their trend; `/database` is gone from the sidebar and the router, and `route-manifest.test.ts`
still passes. `just check` green: 2289 Python, 638 web.

T13 was already built — the stale rule already measured against each node's own reporting interval,
and `truncated` was already surfaced. T14 (`VirtualTable`) left the feature by decision Q4.

The re-expansion confirmed the earlier answer to "is it data or API": **it is the API.** All four
screens already read time-stamped rows and collapse the time axis at the last step with
`argMax(value, timestamp)` or `GROUP BY check_id`. No new table, no migration, no ingestion change —
the series is in rows already being scanned, and two bucketing patterns exist in the codebase to
copy rather than invent.

Six exact out of 48 is the honest picture, not a weakness of the plan: the thin path was read from
the code, and everything after it depends on whether the archetype survives contact with real data.
Marking that costs nothing and is cheaper than pretending otherwise. `design.md`, the wireframes and `plan.md` are approved; this is the last gate before the
first edited file.

**No blocking questions remain.** Q1 — `formatErrors` rendering zero and unknown identically — was
resolved on 2026-09-13: the code stands and `principles.md` rule 2 now names the exception, because
`error_count` is never absent and the ambiguous case cannot occur. The exception carries its
condition, so making that field optional would have to change the formatter in the same commit. Twenty of twenty screens are covered,
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
- `plan.md` written: ordered steps, the first of which is one screen end to end. `docs/design/principles.md`
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

`plan.md` answers that with [ADR-0019](../../decisions/decision-log.md)'s own rule — one thin
end-to-end path, then widen. **The first step is the Services screen alone, and exists to be wrong
cheaply.** A reasonable stopping point comes after filters and context: five screens rebuilt,
filterable and carrying context is a usable product, and the rest can be re-judged then.

### Next action — as it stood at batch 2, kept for its corrections

The current next action is at the top of this file. This section is history: it records two mistakes
worth keeping, one of which propagated into four commits before reading a file corrected it.

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
single response — the batched series read the plan listed as work. **The first two tasks in the plan are
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

Not blocked. T22 onwards — the investigation core, the dashboard surface and the remaining
surfaces — are still `P`, and are expanded one at a time as `plan.md` and ADR-0019 require.

## Validation

`just check` is green: 2342 Python tests, 684 web, 120 browser-SDK, every Go package `ok`.

**What the filters are actually proved by.** `apps/api/tests/test_filters_reach_past_the_cap.py`
seeds, for each screen, one match that ranks last behind more rows than the cap allows. It asserts
the unfiltered read misses it — that assertion is the premise, and without it the suite would pass
against a filter that does nothing — then asserts the filtered read returns it. Neutering the four
filters fails all four tests.

Every other test added here was run against the code before its fix: the delta tests against a
threshold that annotated everything, the structural test against a planted `error.status === 401`,
and each filter test against the statement it was about to change.

**Verified in the browser**, which is where the last two findings came from. Measuring the row
showed no cell overflowing its box after three columns were widened for their deltas. Querying the
API over three window lengths showed the request-rate threshold was annotating almost every row,
which is how it came to be raised from a fifth to a half.

## Residual risks

- **`tsc -b`, not `tsc --noEmit`.** This package uses project references, so a bare `tsc --noEmit`
  silently checks nothing and vitest does not typecheck at all. Two type errors reached the gate
  that way. `pnpm run typecheck` is the only invocation that answers.
- **Search values are written JSON-quoted** — `?with_errors=%22true%22`. That is the router's
  default serialisation and predates this work: `/traces` writes the same shape, and an unquoted
  value pasted by hand still parses. Not changed here, because changing it changes every screen's
  links at once.
- **Deltas are on the services screen only.** AC13 asks that every reading with a rollup behind it
  *be able to* show one, and `shared/delta.ts` plus `previousWindow` are that mechanism. The other
  rollup-backed screens have not been wired to it.
- **A bare `uvicorn` outside compose still sends real mail.** The console provider is set on the
  `api` service in `deploy/compose/docker-compose.yaml`, which is what every live suite reaches. A
  developer who runs the API directly gets `EMAIL_PROVIDER` from their own `.env` and every signup
  spends a real message again. Not fixed here, because making `development` imply `console` in
  `settings.py` would also decide it for anyone deliberately testing the real chain in dev.

## Documentation impact

Three places record "insight-first, not dashboard-first" and contradict the founder's 2026-09-12
decision that both are first-class. They are listed in the specification's Dependencies section and
must be updated as part of this feature: `docs/product/vision.md`,
`docs/product/feature-catalog.md` (F20), and `apps/web/src/app/routes/route-manifest.ts` — the last
of which is enforced by `route-manifest.test.ts`.

## Last updated

2026-09-14 — **reopened.** A screen-by-screen review, read as a customer, produced T48–T68: live
data that refreshes without tearing the screen down, a window that can be absolute, a refresh
somebody can pause, and the smaller gaps the read turned up. All provisional; nothing is expanded
and nothing is edited until the founder approves.

Two exclusions are decisions rather than omissions. **Team membership** — invites, members, roles —
is needed and becomes its own feature, even though the schema already carries the four roles. **The
insight engine** is the next feature, so Findings keeps its healthy state until there is something to
feed it.

Two questions are open: the error-rate thresholds that block T61, and whether billing (T66) and
customer documentation (T67) belong to this phase at all — neither is a console screen, and both are
flagged rather than absorbed.

---

2026-09-13 — the phase closed. T35 through T42 built, `/review-diff` run, the task plan's Outcome
filled in, and `plan.md` ticked with the one unbuilt item left visibly open.

Two wrong diagnoses are recorded because both cost time and both looked convincing: the isolation
suite's `ReadTimeout` was neither rate limiting nor mail, it was a `ModuleNotFoundError` in T35's own
router that killed the reloader's worker while the socket kept accepting; and unsetting `EMAIL_*` in
`just test-py` fixes nothing, because the live suites reach the API over HTTP and the container reads
its own `.env`. The mail fix belongs in the compose file, and is there.
