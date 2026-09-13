# Plan: Console redesign

## Preconditions

- `spec.md` approved 2026-09-12, amended through 2026-09-13. Twenty-one acceptance criteria.
- `design.md` and five wireframe batches approved 2026-09-13.
- No open decisions remain. OD1–OD9 are resolved and recorded.
- `task-plan.md` expands the steps below into files and tests, and is approved before the first edit.

## The sequencing problem this plan exists to solve

The feature grew four times, each time correctly: the dashboard surface, context and filters, the
last four decisions, and the state set. It is now **21 routes, 21 criteria, backend work on several
read endpoints, one genuinely new screen, and ten unbuilt components.**

Built all at once it would be months long, unreviewable, and would prove the archetype only at the
end — where every wrong assumption surfaces together.

[ADR-0019](../../decisions/decision-log.md) already settled how this repository sequences work:
build one thin end-to-end path first, then widen. That decision was made about the ingestion doors
and applies unchanged here.

**So the first step is one screen, end to end, and it exists to be wrong cheaply.**

## Ordered implementation steps

### The thin path — one screen, judged before anything else starts

- [x] `/services` rebuilt on the ranked-entity row: `Sparkline`, the trend column, the severity mark
- [x] Its owned states, each covered by a test — including the empty split (AC6, AC20)
- [x] A test fixing the ranking by absolute error count
- [x] `just check` green; the built screen compared against `prototype-ranked-entities.html`

**Stop here and judge it.** Is the row readable at 13px, does a shared-window sparkline carry the
trend, does the archetype survive contact with real data? If it is wrong, one screen is rebuilt.

### Widen the archetype — four more screens

- [x] Series reads for errors, synthetics, database, infrastructure
- [x] `/errors`, `/synthetics`, merged `/infrastructure` with Databases (IA2)
- [x] Stale is critical, measured against `reporting_interval_seconds`, not a fixed timeout
- [x] `NodeListResponse.truncated` surfaced
- [–] ~~`VirtualTable`~~ — **deferred to its own change record, 2026-09-13.** Nine screens call
      `useVirtualizer` directly and six carry bespoke table markup; extracting the shared table is a
      refactor with no user-visible change, and doing it from four examples while nine exist would
      guess at the shape. It happens once every screen is rebuilt and all nine callers are known.

### Make the screens answerable — filters and context

The cheapest work in the feature and the largest jump in usefulness.

- [x] Filters on the seven screens that have none (AC15) — all over columns already stored
- [x] `device_class` and `route` become filters on RUM, not only breakdown dimensions
- [x] Previous-period delta, one threshold per measure, shown only where significant (AC13, AC14)
- [x] Filtered-empty distinguished from never-had-data everywhere

**A reasonable stopping point.** Five screens rebuilt, filterable and carrying context is a usable
product, and what follows can be judged against what the insight engine turns out to need rather
than against a plan written before a screen existed.

### Investigation core — five screens

- [x] `SpanDetail` exposes `exception_type`, `exception_message`, `deployment_version`
- [x] `TraceSummary` exposes `span_services`, `span_endpoints`, `worst_severity`
- [x] Span `events` as a timeline, `links` as navigation (AC17)
- [x] `/logs` facets as counts; `/traces`; trace detail; service detail with deploy markers (IA3)
- [x] `/service-map` with volume floor and failing-only
- [x] Selection travels between all of them (IA1)

### The dashboard surface

- [x] Overview at `/`, three RED panels, one deploy marker spanning them
- [x] `route-manifest.ts` — the "never the front door" comment records a superseded decision; update
      it and `route-manifest.test.ts` together
- [x] Service detail expanded to the full RED panel set
- [x] Infrastructure USE panels, honest about having no saturation measure

### Surfaces and account — six screens

- [x] RUM with `readings` as a column and `is_route_template` labelled
- [x] Metrics: the catalogue and the histogram reader (AC5)
- [ ] **The conditional heatmap (OD7) — not built, and never became a task.** It is the one item on
      this plan that the task expansion dropped rather than deleted on purpose. `metrics_histogram`
      now has a reader, so the data and the merge it would need exist; what is missing is the panel
      inside Dashboard, drawn only where a customer sends OTLP histograms. Carried forward rather
      than quietly ticked
- [x] Usage rendering `settled: false`, and `services` beside `billed_services`
- [x] Settings ×4, where the dangerous state does most of the work
- [x] Unsaved-changes warning (AC19)

### Gateway and the Findings shell

- [x] Onboarding ends in a confirmation naming the service that arrived (IA4)
- [x] Login, signup, recovery
- [x] Findings feed: structure committed, card provisional; healthy state distinct from
      failed-to-load (AC18)
- [x] `/` moves from Overview to Findings **only when an engine produces findings** — not in this
      feature. Overview still holds `/`, which is the intended end state

### Closing

- [x] `/review-diff`
- [x] `docs/architecture/frontend.md` updated to current truth
- [x] `docs/product/vision.md` and `feature-catalog.md` F20 updated — both still record
      "insight-first, not dashboard-first", which the founder superseded on 2026-09-12. Found in
      **five** places, not three
- [x] Task plan **Outcome** filled in; `status.md` final

## Gates

- [x] Specification approval — 2026-09-12
- [x] Design approval — 2026-09-13
- [x] Plan approval — 2026-09-13
- [x] Task plan approval — before the first edited file
- [x] Implementation
- [x] Tests
- [x] Review — `/review-diff`
- [~] Design QA — partial. Every screen in the final block was checked against the running app, where three defects were found that no test could reach. A pass over every rebuilt screen against its wireframe was not done; the gate still has no implementation
- [x] Documentation and status update

## Validation plan

- `just check` after each group of steps, output and counts read — exit zero alone is not evidence.
- Every state in every rebuilt screen covered by a test (AC6), including the two most often
  collapsed: zero-results against empty, and failed-to-load against healthy.
- A structural test that no feature interprets an HTTP status itself (AC21).
- `verify-tokens.js` and `verify-contrast.js` continue to pass with no drift (AC8).
- `route-manifest.test.ts` continues to pass (AC10).
- Phone width checked at 375px per screen (AC9), not once at the end.
- `just e2e` before the dashboard surface and at the close; `just chaos` is not implicated — this
  feature adds no ingest path.

## Rollout and rollback

Nexovue is pre-deployment: there is no rollout, and rollback is `git revert`.

The risk is not deployment, it is **the first screen being approved and then not being judged.** The
entire value of a thin path is that somebody looks at it and says the density is wrong before four
more screens inherit it. A first screen that ships and is nodded through costs more than no thin
path at all.
