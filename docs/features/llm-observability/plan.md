# Plan: LLM observability (F29)

> **Status:** Awaiting approval

## Preconditions

`spec.md` approved. `design.md` approved.

## Current system

Read from the code, 2026-10-09, not from the design documents:

- `apps/api/src/nexovue_api/metrics/{schemas,queries,service,router}.py` — the closest existing
  precedent, confirmed line-by-line rather than pattern-matched. `queries.py` builds one
  `CATALOGUE_SQL` module constant plus a `catalogue_sql(*, name_match, shape, readable_only)` function
  that splices optional clauses onto two placeholder markers by string replacement — never a runtime
  value concatenated into the statement (`queries.py:138-170`). `service.py:30-78`'s `list_metrics`
  binds parameters only when the matching clause was spliced in, queries through
  `TenantScopedRepository`, and maps rows through a private `_to_summary` (`service.py:81-93`).
  `router.py` has no SQL and no arithmetic — it resolves the window and the project, then calls
  `service`.
- `apps/api/src/nexovue_api/traces/service.py:614-636`, `_to_span` — the one function that already
  parses `attributes_json` into a dict (`values.json_object(row["attributes_json"])`) for every span
  a trace-detail read returns. This is where `is_model_call` is derived, not a new read.
- `apps/api/src/nexovue_api/traces/schemas.py:110-149`, `SpanDetail` — the response class
  `is_model_call` is added to, right after the existing `attributes` field.
- `apps/api/src/nexovue_api/main.py:45,116-...` — routers are mounted with
  `versioned.include_router(<name>_router.router)` inside the app factory; there is no
  auto-discovery to rely on.
- `apps/api/src/nexovue_api/data/clickhouse/base.py` — `TenantScopedRepository`, `READ_SETTINGS`, and
  the structural chokepoint test (`test_tenant_chokepoint_structure.py`) that derives covered tables
  from the schema and parses every module's own source for the literal `tenant_id = {tenant_id:String}`
  predicate. Already covers any new table a statement names; nothing to register.
- `apps/web/src/features/metrics/{api,filters,MetricsScreen}.tsx` — the narrowing-at-the-server
  pattern (`filters.ts`'s `metricFilters`/`metricsNarrowed`, both reused almost verbatim), the
  `useQuery` + `apiClient.get` shape (`api.ts`), and the screen's composition of `FilterBar`,
  `SkeletonRows`, `EmptyState`, `InlineFailure` (`MetricsScreen.tsx:1-19`).
- `apps/web/src/shared/components/DataTable.tsx` — confirmed as the one row-table primitive already
  shared across folders (`auth/account-sections.tsx`, `billing/BillingScreen.tsx`); Profiles'
  `FunctionsTable.tsx` is its own dedicated component, not something to import.
- `apps/web/src/features/traces/SpanDetail.tsx:224`, `Entry` — the "term, value" row component
  already used for `Status` and the RPC fields. No badge/pill/chip primitive exists anywhere under
  `shared/components/` — checked directly, not assumed.
- `apps/web/src/app/router.tsx:206-208,537` — routes are declared with `createRoute({... path: "/metrics" ...})`
  and listed in the router tree; `apps/web/src/app/routes/route-manifest.ts` is the separate sidebar
  list `route-manifest.test.ts` holds against it.
- `apps/api/tests/test_metrics.py` / `test_metrics_live.py` — the offline/live pair every domain
  package carries: a `FakeMetricStore` asserting on bound parameters (never on statement text) for
  tenancy, and a live suite running the same statements against real ClickHouse.
- `tools/gateseed/payload.go`, `GenAISpans` — already seeds three fixture spans (complete, unlabeled
  model, renamed model-name field) through the real ingest path, committed in `ba9311a`. This plan's
  live test reads them; no new fixture work is needed.
- No e2e spec exists for `metrics` (`tools/e2e/tests` has no `metric*.spec.ts`). Matched here rather
  than adding one this feature would be the first catalogue-style screen to carry.

## Change required

Pointer only, per `design.md`: a new `llm_observability` domain package mirroring `metrics`'s shape,
one new web screen, and one additive field on the existing trace-detail response. No new ingestion,
no schema change, no migration.

## Impact

| Area | What changes |
|---|---|
| Files | New: `apps/api/src/nexovue_api/llm_observability/{__init__,schemas,queries,service,router}.py`, `apps/web/src/features/llm-observability/{api,filters,LLMCallsScreen}.tsx`. Modified: `apps/api/src/nexovue_api/main.py`, `apps/api/src/nexovue_api/traces/{schemas,service}.py`, `apps/web/src/features/traces/SpanDetail.tsx`, `apps/web/src/app/router.tsx`, `apps/web/src/app/routes/route-manifest.ts` |
| Services | `apps/api` only. `apps/receiver`/`apps/workers` untouched. |
| Schema / migrations | None. |
| UI | One new sidebar entry (*Signals* group, after Profiles), one new screen, one new row on the existing trace-detail view. |
| Tests | New: `test_llm_observability.py`, `test_llm_observability_live.py`, `apps/web/src/features/llm-observability/*.test.ts`. Modified: `test_traces.py`/`test_traces_live.py` (the new field), `route-manifest.test.ts` runs unmodified and must keep passing. |

## Reuse

`TenantScopedRepository`, `READ_SETTINGS`, `CurrentReader`/`TenantRepository`/`require_readable_project`
dependencies, `resolve_window`, `receive_window`, `values.text`/`values.json_object`, the
chokepoint/tenancy structural tests (no registration needed — they derive coverage from the schema
and the source tree), `FilterBar`/`SkeletonRows`/`EmptyState`/`InlineFailure`/`DataTable`,
`useLiveWindow`/`useSelection`/`useWindowPhrase`, `apiClient`, `filterCacheKey`/`parseFilterValues`,
the existing `Entry` component, and `gateseed`'s already-seeded `GenAISpans` fixtures. Nothing in this
list is rebuilt.

## Gaps

Genuinely new: the `llm_observability` package itself (no file of it exists), the
`GEN_AI_ATTRIBUTE_PREFIX`/`GEN_AI_MODEL_ATTRIBUTE`/`GEN_AI_INPUT_TOKENS_ATTRIBUTE`/
`GEN_AI_OUTPUT_TOKENS_ATTRIBUTE` constants design.md names, the `JSONAllPaths`-based identification
clause (no existing query in this codebase uses that function — confirmed by grep), the
`is_model_call` field end to end, the new screen and its two supporting files, and the sidebar/route
registration.

## Steps and tasks

### Step 1 — the read path exists and answers correctly, provable without a screen

*Judgeable:* `curl` (or the OpenAPI docs at `/api/v1/docs`) against `GET /api/v1/llm/calls` with a
project that holds the `gateseed` fixtures returns three grouped rows: one complete, one under
`"unlabeled model"`, and the renamed-field one also under `"unlabeled model"` — matching AC29.2/29.3/29.6
directly, before any UI exists.

- [ ] **T1** — `apps/api/src/nexovue_api/llm_observability/schemas.py` — `LLMCallSummary` (service_name,
      span_name, model_name, call_count, p50_ms, p95_ms, error_count, input_tokens, output_tokens;
      token fields `int | None`, never defaulted to `0`) and `LLMCallListResponse` (window_start,
      window_end, calls) · proved by `test_llm_observability.py::test_schema_shapes`
- [ ] **T2** — `apps/api/src/nexovue_api/llm_observability/queries.py` — the four `GEN_AI_*` constants,
      the module-constant `LLM_CALLS_SQL` (tenant predicate repeated literally, per the chokepoint's
      own convention), and an `llm_calls_sql(...)` splice function if any optional narrowing is needed
      · proved by `test_llm_observability.py::test_llm_calls_sql_binds_the_tenant_predicate` (asserts
      the literal string, matching `test_metrics.py`'s own technique)
- [ ] **T3** — `apps/api/src/nexovue_api/llm_observability/service.py` — `list_llm_calls`, the
      unlabeled-model fallback, and per-field absence handling (a span with no token attribute
      contributes to `call_count` and never to a token sum) · proved by
      `test_llm_observability.py::test_a_span_with_no_token_attribute_is_not_summed_as_zero` and
      `::test_a_span_missing_the_model_attribute_groups_as_unlabeled`
- [ ] **T4** — `apps/api/src/nexovue_api/llm_observability/router.py` — `GET /llm/calls`, mirroring
      `metrics/router.py`'s `list_metrics` signature exactly · proved by
      `test_llm_observability.py::test_the_route_requires_a_readable_project`
- [ ] **T5** — `apps/api/src/nexovue_api/main.py` — one `include_router` line · proved by the route
      appearing in the OpenAPI schema (`test_the_console_document_still_holds_everything`'s existing
      coverage, no new test needed — it already fails on an undocumented route)
- [ ] **T6** — `apps/api/tests/test_llm_observability_live.py` — the same statement against real
      ClickHouse, reading `gateseed`'s existing `GenAISpans` fixtures, asserting AC29.2/29.3/29.6
      directly against live data · proved by the suite itself passing against the dev stack

### Step 2 — the trace-detail view marks a model call, provable in the existing trace waterfall

*Judgeable:* open any trace holding a `gateseed`-seeded gen_ai span in the development console; its
`SpanDetail` panel shows a "Model call" row the same span lacked before this step.

- [ ] **T7** — `apps/api/src/nexovue_api/traces/schemas.py` — `is_model_call: bool = False` on
      `SpanDetail`, after `attributes` · proved by `test_traces.py::test_a_span_with_a_gen_ai_attribute_is_marked_a_model_call`
- [ ] **T8** — `apps/api/src/nexovue_api/traces/service.py` — `_to_span` derives it from the already-parsed
      `attributes` dict via the shared `GEN_AI_ATTRIBUTE_PREFIX` imported from
      `llm_observability.queries` (not redeclared) · proved by the same test as T7, plus
      `test_traces_live.py::test_a_real_gen_ai_span_is_marked_a_model_call`
- [ ] **T9** — `apps/web/src/features/traces/SpanDetail.tsx` — one `Entry` row, shown only when
      `span.is_model_call`, reading the model name when present or "Unlabeled model" when not · proved
      by `SpanDetail.test.ts`'s existing suite, extended with one case

### Step 3 — the screen exists and is reachable

*Judgeable:* the founder opens **LLM calls** from the sidebar and sees the three `gateseed` rows,
correctly labelled, with the two filters working.

- [ ] **T10** — `apps/web/src/features/llm-observability/api.ts` — `LLMCallSummary`/`LLMCallListResponse`
      types (wire field names unconverted, per `docs/engineering/frontend.md`) and `useLLMCalls`,
      mirroring `metrics/api.ts`'s `useMetricCatalogue` exactly · proved by a query-key test mirroring
      `MetricsView.test.ts`'s own
- [ ] **T11** — `apps/web/src/features/llm-observability/filters.ts` — service/model narrowing,
      mirroring `metrics/filters.ts` · proved by a filters test mirroring that file's own
- [ ] **T12** — `apps/web/src/features/llm-observability/LLMCallsScreen.tsx` — `FilterBar` +
      `DataTable` + `SkeletonRows`/`EmptyState`/`InlineFailure`, the three states named in `design.md`'s
      *UI structure* (empty / zero-results-from-filter / partial) · proved by
      `LLMCallsScreen.test.ts` covering all three
- [ ] **T13** — `apps/web/src/app/router.tsx` — one `createRoute` entry, path `/llm` · proved by
      `route-manifest.test.ts` passing once T14 also lands (the test fails if either side is missing
      the other)
- [ ] **T14** — `apps/web/src/app/routes/route-manifest.ts` — one sidebar entry, *Signals* group,
      after Profiles · proved by the same `route-manifest.test.ts`

## New risks

- `JSONAllPaths` is new to this codebase's query vocabulary — no existing statement uses it. T2's own
  test and T6's live run are what actually prove its cost is in the same class as `metrics`'s five-table
  scan, not an assumption carried from `design.md`.
- The cost attribute stays unset per `design.md`'s own decision; T1-T6 ship with `cost` always absent.
  Not a defect — stated here so it is not mistaken for one during review.

## Questions

None blocking. The one open question from `design.md` (what real attribute name carries cost, if any)
is explicitly deferred, not something this plan needs answered to ship.

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|
| 1 | What attribute name, if any, real SDKs use for gen_ai cost | No | `spec.md` open decisions, if evidence ever surfaces |

## Gates

- [x] Specification approval
- [x] Design approval
- [ ] **Plan approval — before the first edited file**
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

`just check` after each step, read for output and counts, not just exit code. Step 1 adds
`test_llm_observability.py` (offline) and `test_llm_observability_live.py` (against the dev stack,
reading `gateseed`'s existing fixtures — no new seeding). Step 2 extends `test_traces.py`/
`test_traces_live.py`/`SpanDetail.test.ts`. Step 3 adds web unit tests and must keep
`route-manifest.test.ts` passing. No e2e spec, matching `metrics`'s own precedent — see *Current
system*. No chaos or load check: no ingestion path, no store write path, nothing this plan touches
affects durability.

## Rollout and rollback

Pre-deployment; no rollout mechanism exists yet (F37). The real risk named per the template's own
prompt: step 1 shipping and nobody looking at the raw endpoint before steps 2-3 build on it. Each
step's "judgeable" line above exists specifically so that does not happen silently.

## Outcome

Filled in when the work completes.
