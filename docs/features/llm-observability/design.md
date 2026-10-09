# Design: LLM observability (F29)

## Status

Draft

## Context and goal

`spec.md` is approved. The gap is entirely a screen and a read path — `gen_ai.*` attributes already
land in `spans.span_attrs` through the existing traces door, confirmed live and reconfirmed in
`market-analysis-2026-09.md` §13. This design adds a new, self-contained API domain package
(`llm_observability`, mirroring `metrics`'s shape exactly) and one new web screen, plus a small,
additive change to the existing trace detail response. Nothing in `apps/receiver` or `apps/workers`
changes.

## User experience

A developer who instruments an LLM call opens a new sidebar entry, **LLM calls**, placed in the
*Signals* group beside Traces and Profiles. The screen is a single filterable table — service, span
name, model (or "unlabeled model"), call count, p50/p95 latency, error rate, tokens, cost — over the
project's shared time window. No drill-down screen: a row's own span already exists in the trace
waterfall, so clicking a row's service+span name narrows the existing Traces screen to it rather than
duplicating a detail view. Opening any trace that contains a model call shows a small badge on that
span in the existing `SpanDetail` view, computed by the identical rule the table uses.

## UI structure

**Screen:** `apps/web/src/features/llm-observability/LLMCallsScreen.tsx`, structured like
`MetricsScreen.tsx`: `FilterBar` (service, model name — both narrow the request, matching `metrics`'s
narrowing-at-the-server pattern, not a client-side filter over a capped page), `SkeletonRows` while
loading, `EmptyState` when the window holds no model calls, `InlineFailure` on a fetch error. The
table itself reuses `shared/components/DataTable.tsx` — confirmed as the one row-table primitive
already shared across feature folders (`auth/account-sections.tsx`, `billing/BillingScreen.tsx`),
not something this feature would be the first to pull out of a single screen. Checked and rejected:
Profiles' `FunctionsTable.tsx` is its own dedicated component, not a shared one, so it is a sibling
to build like, not a thing to import from.

**States owned by the screen:**
- *Empty* — "No model calls seen in `<window phrase>`." States the window, per rule 3's "say why and
  what to do next"; does not suggest instrumentation steps here, since `docs/product/setup/` already
  owns onboarding copy.
- *Zero results from a filter* — distinct from empty: "No model calls matched `<filter>`," with a
  clear-filter action, per rule 3's explicit requirement that the two never collapse into one.
- *Partial* — a row missing tokens or cost renders an em dash in those cells only; the row still
  renders (rule 3, "partial").
- 401/403/404/429/5xx — all owned centrally, nothing new here (rule 3's "owned centrally" table).

**Reused, not rebuilt:** `FilterBar`, `SkeletonRows`, `EmptyState`, `InlineFailure`,
`shared/format/number.ts`'s em-dash convention, `useLiveWindow`/`useSelection`/`useWindowPhrase`,
`DataTable`. Nothing here justifies a new control (rule 4).

**`SpanDetail.tsx` change:** one boolean field, `is_model_call`, read from the existing `attributes`
the component already flattens — shown as one more `Entry` row (the component this file already uses
for "Status" and the RPC fields, `SpanDetail.tsx:224`), reading `"Model call" → <model name, or
"unlabeled model">` when true, omitted entirely when false. Checked first: no badge, pill, or chip
primitive exists anywhere under `shared/components/`, so the honest options were build one or reuse
`Entry`; `Entry` already carries exactly this "label, one value" shape, so nothing new is built.

## Proposed design

### The one real design wrinkle: one rule, two execution contexts

FR29.1/FR29.5 require identification to be the *same* rule wherever it runs. It cannot be the same
*code*, because one side runs as SQL inside ClickHouse (the aggregate table) and the other runs in
Python over an already-fetched attribute dict (the trace-detail badge, in `traces/service.py`, which
already parses `attributes_json` per span). The design makes them the same rule by sharing one
constant rather than one function:

```python
# nexovue_api/llm_observability/queries.py
GEN_AI_ATTRIBUTE_PREFIX: Final = "gen_ai."
```

SQL side: `arrayExists(p -> startsWith(p, {gen_ai_prefix:String}), JSONAllPaths(span_attrs))`.
`JSONAllPaths` (ClickHouse ≥ 24.8, confirmed against the pinned 26.3 this repository runs) returns
every concrete path present on a row's JSON value — exactly "any key under this prefix," with no
enumeration of known attribute names, matching FR29.1's genericity requirement precisely.

Python side (`traces/service.py`'s existing per-span mapping):
`any(key.startswith(GEN_AI_ATTRIBUTE_PREFIX) for key in attributes)`, importing the same constant
from `llm_observability.queries` rather than redeclaring it — one string, one place, both readers.

### Model name and the attribute this feature currently reads

FR29.2/AC29.6 require the model-name field to be a single, named, easily-updated pointer — not
hardcoded inline — because the convention has already renamed one field once
(`gen_ai.system` → `gen_ai.provider.name`, Aug 2025) and the current model-name field
(`gen_ai.request.model`, confirmed live against the semantic-conventions registry, 2026-10-09) sits
in the same still-Development namespace:

```python
GEN_AI_MODEL_ATTRIBUTE: Final = "gen_ai.request.model"
GEN_AI_INPUT_TOKENS_ATTRIBUTE: Final = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS_ATTRIBUTE: Final = "gen_ai.usage.output_tokens"
```

A span identified as a model call (by the prefix rule above) but missing `GEN_AI_MODEL_ATTRIBUTE`
groups under an explicit `"unlabeled model"` bucket (AC29.2, AC29.3) — never dropped, never guessed.
Renaming any one constant, the day the convention stabilises and renames it again, is the entire
migration this feature ever needs (NFR29.2).

**Open question this design surfaces rather than resolves silently: there is no standard "cost"
attribute.** OTel's `gen_ai.*` convention standardises token counts, not a cost figure — confirmed
while building this feature's own test fixtures. FR29.4/AC29.4 ask for cost "when reported," which
this design honours literally: a `GEN_AI_COST_ATTRIBUTE` constant exists as a single pointer, exactly
like the model and token ones, but **no verified real-world attribute name is known to point it at
yet**. Left unset (reading nothing, always absent, always an em dash) until the plan step finds
real evidence of what SDKs actually emit, rather than inventing a plausible-sounding name now. This
is the one piece of FR29.4 that ships inert on day one, by design, not by oversight.

### Aggregate query shape

One statement, module-level, in `llm_observability/queries.py`, following `metrics/queries.py`'s
own convention (the tenant predicate repeated literally, not factored through a helper, because the
structural test reads string literals):

```sql
SELECT
    service_name,
    span_name,
    ifNull(nullIf(toString(getSubcolumn(span_attrs, {model_attr:String})), ''), 'unlabeled model')
        AS model_name,
    count() AS call_count,
    quantile(0.5)(duration_ms) AS p50_ms,
    quantile(0.95)(duration_ms) AS p95_ms,
    countIf(status_code = 'ERROR') AS error_count,
    sumIf(toFloat64OrNull(toString(getSubcolumn(span_attrs, {input_tokens_attr:String}))),
          ...) AS input_tokens,
    ...
FROM spans
WHERE tenant_id = {tenant_id:String}
  AND project_id = {project_id:String}
  AND timestamp >= {from_time:DateTime64(3)} AND timestamp < {to_time:DateTime64(3)}
  AND received_at >= {received_from:DateTime64(3)} AND received_at < {received_to:DateTime64(3)}
  AND arrayExists(p -> startsWith(p, {gen_ai_prefix:String}), JSONAllPaths(span_attrs))
GROUP BY service_name, span_name, model_name
ORDER BY call_count DESC
```

(Exact aggregation of "reported vs. absent" for the sum columns — `sumIf` needs a presence flag
alongside the value, not just a null-coalesced zero, to honour "never a false zero" — is a plan-level
task, not re-litigated here; the shape above is illustrative of the statement's structure, not the
literal final SQL.)

### New endpoint

`GET /llm/calls` — `llm_observability/router.py`, mirroring `metrics/router.py`'s
`list_metrics` exactly: `CurrentReader`, `TenantRepository`, `require_readable_project`,
`resolve_window`, optional `service`/`model` narrowing query params (server-side, like metrics'
`name` filter — never a client-side filter over a capped page).

## Data flow and interfaces

```
customer's instrumented service (gen_ai.* spans)
        │  (unchanged — existing OTLP traces door)
        ▼
apps/workers  trace-assembly  (unchanged)
        ▼
ClickHouse `spans` (unchanged schema)
        │
        ├── GET /llm/calls ──► llm_observability/{router,service,queries}.py ──► new screen
        └── GET /traces/{id} ──► traces/service.py (+ is_model_call per span) ──► existing SpanDetail badge
```

No new table, no new topic, no new worker pipeline, no new migration.

## Data and migration impact

None. Read-only over the existing `spans` table and its existing `JSON` column.

## Performance and scale

`JSONAllPaths` introspects the full JSON value per row; this is a full-blob scan, same cost class as
`metrics/queries.py`'s own five-table `UNION ALL` (both read `span_attrs`/the metric tables' full
attribute set per row, not a promoted column). Bounded identically: the shared `READ_SETTINGS`
row/time caps (`data/clickhouse/base.py`), the project's own time window, and no new exemption
(NFR29.3). No separate load test is planned beyond what the existing tenancy-chokepoint and
read-cap tests already enforce structurally for every new statement.

## Failure handling

- ClickHouse unreachable: the same deferred-connection, 503-on-first-use behaviour every other
  screen already has (`apps/api/src/nexovue_api/lifespan.py:72`, also documented in
  `docs/architecture/current-system-reference.md`) — nothing bespoke.
- A malformed or non-numeric token/cost attribute value: treated as absent for that span (contributes
  to call count, not to the sum), never raises and never silently coerces to zero.
- An attribute renamed out from under `GEN_AI_MODEL_ATTRIBUTE`: degrades to the "unlabeled model"
  bucket (AC29.6) — proved by a fixture carrying exactly this shape, already seeded via `gateseed`.

## Security, tenancy, and privacy

Every new ClickHouse statement carries the literal `tenant_id = {tenant_id:String}` predicate and is
a module-level constant, satisfying the existing structural chokepoint test with no exemption.
`require_readable_project` gates the endpoint identically to `metrics`'s own read route — a viewer
role, not a mutating one, needs no stronger gate. No prompt or completion content is read or stored
by this feature (spec's own *Out of scope*), so no new masking or PII surface is introduced.

## Observability and operations

No new structured log fields beyond the API's existing per-request logging. No new alert, no new
dashboard — this feature does not touch F21.

## Alternatives and tradeoffs

- **A dedicated trace-detail screen for model calls**, rather than reusing the existing trace
  waterfall with a badge. Rejected: the spec's own scope explicitly treats agent-trajectory views
  (which would need this) as a separate feature; building a second detail surface now for a feature
  that deliberately excludes trajectories would be building ahead of the decision that justifies it.
- **A promoted ClickHouse column for `gen_ai.request.model`**, mirroring how `http.route` is promoted
  for traces, instead of reading the JSON attribute generically every query. Rejected per the spec's
  own *Out of scope*: a promoted column is exactly the "frozen metric contract" the convention's
  instability argues against — a column survives a rename worse than a constant does, since it needs
  a migration, not an edit.
- **Hardcoding a plausible cost attribute name now** (e.g. a vendor-specific one seen in passing)
  rather than leaving `GEN_AI_COST_ATTRIBUTE` unset. Rejected: no verified evidence exists for what
  real SDKs actually emit, and AGENTS.md's research standard is to say so rather than guess in the
  same confident voice as a sourced answer.

## Specification impact

None. Every functional requirement in `spec.md` is satisfied as written; the cost-attribute gap is a
*discovery* the spec already anticipated structurally (FR29.4's "only when present"), not a
contradiction of it.
