# Status: LLM observability (F29)

## Lifecycle status

Built, reviewed, committed and pushed — all three plan steps implemented, tested, and verified live
against the dev stack. Production-readiness assessment not yet run (nexovue is pre-deployment;
F37, the self-hosted install, remains the actual Phase-1 critical path this feature was prioritized
ahead of).

## Current task

None.

## Completed

- Specification approved, agent-trajectory views and a frozen metric contract both explicitly
  deferred with reasoning on record.
- Design approved: a new `llm_observability` domain package mirroring `metrics`, one new screen, one
  additive field on the existing trace-detail response. `JSONAllPaths` (ClickHouse ≥ 24.8, confirmed
  against the pinned 26.3) identified as the mechanism for generic `gen_ai.*` detection.
- Fixtures already seeded live, ahead of implementation: `tools/gateseed`'s `GenAISpans` (committed
  `ba9311a`) sends three cases — complete, unlabeled model, renamed model-name field — through the
  real ingest path every `just check` run.
- A real masking defect found and fixed while seeding those fixtures: the credential-keys rule
  destroyed `gen_ai.usage.input_tokens`/`output_tokens` as if they were credentials (committed
  `1c87e52`).
- **Step 1** — `llm_observability/{schemas,queries,service,router}.py`, mounted in `main.py`. Verified
  by curl against a real session: the three `gateseed` groups, correctly labelled.
- **Step 2** — `SpanDetail.is_model_call`, derived in `traces/service.py` from the same shared
  `has_gen_ai_attribute` the screen's Python side needs. A real bug in this feature's own new code was
  found and fixed before it shipped: the first version checked a flat `gen_ai.`-prefixed key, but
  `values.json_object` nests a dotted OTLP key by segment, so it matched nothing against real data —
  caught by the live test, fixed to check top-level `"gen_ai"` membership. Verified live: the
  `SpanDetail` panel shows "Model call → claude-fable-5-1" on a real `gateseed` span.
- **Step 3** — the `LLMCallsScreen` (`apps/web/src/features/llm-observability/`), sidebar entry
  (*Signals*, after Profiles), and `/llm` route. Verified live: three correctly-labelled rows,
  service/model search narrowing both the list and the URL, and the span-name link narrowing Traces to
  exactly that trace.
- Two structural gates the plan did not anticipate, caught by `just check` once the route existed and
  fixed: `/llm` added to `ratelimit/policy.py`'s `TELEMETRY_PATHS` (it reads ClickHouse and was
  otherwise falling on the uncapped default tier), and `GET /llm/calls` added to
  `test_read_surface_structure.py`'s `THE_SURFACE` and to `docs/product/setup/api.md`'s customer-facing
  table (the route takes `CurrentReader`, so an API token can reach it).
- `/review-diff` run: no open findings. One nit (a hardcoded `"en-US"` locale literal instead of the
  shared `NUMBER_LOCALE` constant) was found and fixed inline.

## Next action

Founder: look at the LLM calls screen's two search boxes (the one deviation from `design.md`, see
*Risks*). A production-readiness check (`/readiness-check`) when F29 is next in line for a rollout.

## Open decisions

None blocking. Non-blocking, recorded in `plan.md`'s own *Questions*: what attribute name, if any,
real SDKs use for `gen_ai` cost — deferred, not needed to ship steps 1-3.

## Blocked

Nothing.

## Risks

- `JSONAllPaths`'s cost was asserted by design as being in the same class as `metrics`'s own
  five-table scan; `just check`'s full suite (including the live tests) ran without a timeout or a
  slow-query warning, but no dedicated load test was run beyond what the plan's own *Validation plan*
  said one wasn't needed.
- Cost ships inert (always absent) until real-world evidence of an attribute name exists. Intentional,
  not a gap to close before shipping.
- `FilterBar` was not used for `service`/`model` narrowing (see `plan.md`'s Outcome) — a deviation from
  `design.md`'s literal wording, made because `FilterBar` has no free-text control kind and both filters
  are unbounded substring matches. Worth the founder's eyes on the actual screen, not just the record,
  since it is the one place this build differs from what was approved.

## Validation

`just check` passes in full: Go, Python (4174 passed, 3 skipped), browser-sdk (120 passed) and web
(1350 passed) test suites, `buf lint`/`buf breaking`. Live-verified in the browser against the dev
stack (`gateseed`'s three fixtures) for all three steps — see *Completed* above.

## Specification impact

None.

## Documentation impact

- `spec.md` had two citation errors, caught and corrected while writing `design.md`: a reference to
  `docs/engineering/api.md` for `READ_SETTINGS` (actually `data/clickhouse/base.py`) and to
  `docs/architecture/backend.md` for the 503-on-first-use ClickHouse behavior (actually
  `apps/api/src/nexovue_api/lifespan.py`). Both fixed in the same commit as `design.md` (`2478fc7`).
- `docs/product/setup/api.md` gained one row: `GET /llm/calls` on the customer-facing token read
  surface (see *Completed* above).

## Last updated

2026-10-09
