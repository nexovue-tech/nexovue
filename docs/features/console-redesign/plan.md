# Plan: Console redesign

## Preconditions

- `spec.md` approved 2026-09-12, amended through 2026-09-13. Twenty-one acceptance criteria.
- `design.md` and five wireframe batches awaiting approval. **This plan does not begin until they
  are approved** — it is written now so the founder can judge the scope before approving.
- No open decisions remain. OD1–OD9 are resolved and recorded.

## The sequencing problem this plan exists to solve

The feature grew four times, each time correctly: the dashboard surface, context and filters, the
last four decisions, and the state set. It is now **21 routes, 21 criteria, backend work on nearly
every read endpoint, one genuinely new screen, and ten unbuilt components.**

Built as one branch, it would be months long, unreviewable, and would prove the archetype only at the
end — which is where every wrong assumption would surface at once.

[ADR-0019](../../decisions/decision-log.md) already settled how this repository sequences work:
build one thin end-to-end path first, then widen. That decision was made about the ingestion doors
and it applies unchanged here.

**So: phase 1 is one screen, end to end, and it exists to be wrong cheaply.**

## Ordered implementation tasks

- [x] Specification approval — 2026-09-12
- [ ] **Design approval** — `design.md` and batches 1–5
- [ ] Plan approval — this document

### Phase 1 — One screen, end to end *(the walking skeleton)*

Services only. Nothing else starts until this ships and is judged.

- [x] ~~`ServiceHealth`/`EndpointHealth` expose latency~~ — **already built**, found 2026-09-13
- [x] ~~The service list returns its series in one response~~ — **already built**: `GET /services/trend`
      with no service named returns one `ServiceTrend` per service, server-bucketed
- [ ] `Sparkline` in `shared/components/`, shared window across a screen (AC2, AC3)
- [ ] `/services` rebuilt on the ranked-entity row: status, rate, errors, p95, trend
- [ ] Its owned states, each covered by a test: loading, empty, zero-results, partial, dangerous (AC6, AC20)
- [ ] Ranked by absolute error count, and a test that fixes that ordering
- [ ] `just check` green; the prototype and the built screen compared side by side

**Phase 1 answers:** is the row readable at 13px, does a shared-window sparkline carry the trend,
is the batched series read fast enough, and does the archetype survive contact with real data.

**If phase 1 is wrong, one screen is rebuilt.** That is the entire purpose of the phase.

### Phase 2 — Widen the archetype *(four screens)*

- [ ] Series reads for errors, synthetics, database, infrastructure
- [ ] `/errors`, `/synthetics`, merged `/infrastructure` with Databases (IA2)
- [ ] Stale is critical, measured against `reporting_interval_seconds`, not a fixed timeout
- [ ] `NodeListResponse.truncated` surfaced
- [ ] `VirtualTable`, now overdue at nine `useVirtualizer` callers

### Phase 3 — Make the screens answerable *(filters and context)*

The cheapest work in the feature and the largest jump in usefulness.

- [ ] Filters on the seven screens that have none (AC15) — all over columns already stored
- [ ] `device_class` and `route` become filters on RUM, not only breakdown dimensions
- [ ] Previous-period delta, one threshold per measure, shown only where significant (AC13, AC14)
- [ ] Filtered-empty distinguished from never-had-data everywhere

### Phase 4 — Investigation core *(five screens)*

- [ ] `SpanDetail` exposes `exception_type`, `exception_message`, `deployment_version`
- [ ] `TraceSummary` exposes `span_services`, `span_endpoints`, `worst_severity`
- [ ] Span `events` as a timeline, `links` as navigation (AC17)
- [ ] `/logs` facets as counts; `/traces`; trace detail; service detail with deploy markers (IA3)
- [ ] `/service-map` with volume floor and failing-only
- [ ] Selection travels between all of them (IA1)

### Phase 5 — The dashboard surface

- [ ] Overview at `/`, three RED panels, one deploy marker spanning them
- [ ] `route-manifest.ts` — the "never the front door" comment records a superseded decision;
      update it and `route-manifest.test.ts` together
- [ ] Service detail expanded to the full RED panel set
- [ ] Infrastructure USE panels, honest about having no saturation measure

### Phase 6 — Surfaces and account *(six screens)*

- [ ] RUM with `readings` as a column and `is_route_template` labelled
- [ ] Metrics: the catalogue, plus the histogram reader (AC5) and the conditional heatmap
- [ ] Usage rendering `settled: false`, and `services` beside `billed_services`
- [ ] Settings ×4, where the dangerous state does most of the work
- [ ] Unsaved-changes warning (AC19)

### Phase 7 — Gateway and the Findings shell

- [ ] Onboarding ends in a confirmation naming the service that arrived (IA4)
- [ ] Login, signup, recovery
- [ ] Findings feed: structure committed, card provisional; healthy state distinct from
      failed-to-load (AC18)
- [ ] `/` moves from Overview to Findings **only when an engine produces findings** — not in this
      feature

### Closing

- [ ] `/review-diff`
- [ ] `docs/architecture/frontend.md` updated to current truth
- [ ] `docs/product/vision.md` and `feature-catalog.md` F20 updated — both still record
      "insight-first, not dashboard-first", which the founder superseded on 2026-09-12
- [ ] `status.md` final

## Validation plan

- `just check` at the end of every phase, output and counts read — exit zero alone is not evidence.
- Every state in every rebuilt screen covered by a test (AC6), including the two most often
  collapsed: zero-results against empty, and failed-to-load against healthy.
- A structural test that no feature interprets an HTTP status itself (AC21).
- `verify-tokens.js` and `verify-contrast.js` continue to pass with no drift (AC8).
- `route-manifest.test.ts` continues to pass (AC10).
- Phone width checked at 375px per screen (AC9), not once at the end.
- `just e2e` before phase 5 and at the close; `just chaos` is not implicated — this feature adds no
  ingest path.

## Rollout and rollback

Nexovue is pre-deployment: there is no rollout, and rollback is `git revert`.

The risk is not deployment, it is **phase 1 being approved and then not being judged.** The entire
value of a walking skeleton is that somebody looks at it and says the density is wrong before four
more screens inherit it. A phase 1 that ships and is nodded through costs more than no phase 1 at
all.

**Recommended stop point:** after phase 3. At that point five screens are rebuilt, filterable, and
carry context — which is a usable product — and the remaining phases can be re-judged against
whatever the insight engine turns out to need. Committing to all seven phases now is committing to a
plan written before a single screen was built.
