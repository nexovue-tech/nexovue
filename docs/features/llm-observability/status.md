# Status: LLM observability (F29)

## Lifecycle status

Approved — spec and design both approved; plan awaiting approval.

## Current task

None. Waiting on plan approval before the first file is edited.

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

## Next action

Founder: approve `plan.md`, or say what to change before implementation starts.

## Open decisions

None blocking. Non-blocking, recorded in `plan.md`'s own *Questions*: what attribute name, if any,
real SDKs use for `gen_ai` cost — deferred, not needed to ship steps 1-3.

## Blocked

On plan approval.

## Risks

- `JSONAllPaths` is new to this codebase's query vocabulary; its cost is asserted by design, not yet
  measured against this product's own data — step 1's live test is what actually proves it.
- Cost ships inert (always absent) until real-world evidence of an attribute name exists. Intentional,
  not a gap to close before shipping.

## Validation

Not yet run — implementation has not started.

## Specification impact

None.

## Documentation impact

`spec.md` had two citation errors, caught and corrected while writing `design.md`: a reference to
`docs/engineering/api.md` for `READ_SETTINGS` (actually `data/clickhouse/base.py`) and to
`docs/architecture/backend.md` for the 503-on-first-use ClickHouse behavior (actually
`apps/api/src/nexovue_api/lifespan.py`). Both fixed in the same commit as `design.md` (`2478fc7`).

## Last updated

2026-10-09
