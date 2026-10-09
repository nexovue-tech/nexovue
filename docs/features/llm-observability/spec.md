# Feature: LLM observability (F29)

## Status

Proposed

## Problem

A customer building on an LLM instruments it with OpenTelemetry's `gen_ai.*` semantic conventions
(or a framework that does this for them), and that data already lands in nexovue's `spans` table —
confirmed 2026-09-07, reconfirmed in `market-analysis-2026-09.md` §13. There is no way to see it.
A `gen_ai.*` attribute is visible today only as an unlabeled row in a span's generic attributes
panel (`SpanDetail.tsx`), indistinguishable from every other attribute on that span. There is no
list of which endpoints call a model, no per-model or per-endpoint rollup, no token or cost total,
and no way to tell, without opening spans one at a time, whether a service's calls to a model are
slow or failing.

## Goal

A developer can see, without reading raw span attributes by hand: which of their services and
endpoints call a model, which model, how often, how slow, how much it costs or how many tokens it
used when that is reported, and whether those calls are erroring — using data already arriving
today, with no new ingestion.

## Target users

- **P1 — Dev, primary.** Debugging and understanding LLM cost/latency the same way they already do
  a slow database query, on a service they built.
- **P2 — Indie, secondary.** Same need, smaller scale, same screen.
- **P3 — Owner is explicitly not served.** No plain-language translation of "which model" or "token
  cost" is owed to a non-technical owner by this feature. The owner status page (F7) is a separate,
  unbuilt surface and this spec does not feed it.

## Scope

- A new screen listing model calls, aggregated by service, endpoint, and model: call count,
  p50/p95 latency, error rate, over the project's shared time window every other screen already
  uses.
- Token usage and cost, shown per group and summed, **only when the underlying attribute is
  present** on the spans being summed.
- Generic identification: a span is a "model call" because it carries any attribute in the
  OTel `gen_ai.*` namespace — never because its service or endpoint name matched a hand-maintained
  list.
- A visible label on a span already showing in the existing trace waterfall (`SpanDetail.tsx`) when
  that span is a model call — the existing detail view gains a badge, not a second place a developer
  has to think to look.

## Out of scope

- **Agent trajectory views** — tool-call sequences, reasoning chains, human-handoff points spanning
  one agent run. Deferred, not merged into this spec: `market-analysis-2026-09.md` found no
  competitive pressure for it among the peer set nexovue is actually priced against (§B.9), the
  OTel conventions for agent-specific spans are less settled than the base `gen_ai.*` ones this spec
  already treats as unstable, and the right UI for a trajectory — a specialized multi-span view, not
  an aggregate list — is different enough from this feature's screen to deserve its own spec rather
  than widen this one's scope.
- **A frozen metric contract** — promoted ClickHouse columns or a fixed schema for `gen_ai.*`
  fields. `market-analysis-2026-09.md` §13 is explicit: the convention moved out of OTel core into
  its own repository with no tagged release yet, and has already broken once
  (`gen_ai.system` → `gen_ai.provider.name`, Aug 2025). Every functional requirement below reads
  attributes generically, at query time, specifically so nothing here needs re-cutting when the spec
  stabilizes.
- **Prompt or completion content.** Nothing in this feature reads or stores what was asked or
  answered. Masking/privacy treatment of that content, if nexovue ever shows it, is a separate
  decision this spec does not make.
- **ML observability** (data drift, model accuracy, feature monitoring). A different signal shape
  entirely — not span data — and not on the feature catalogue.

## User requirements

- US29.1 — As a Dev, I can see a list of the model calls my services make, without searching for
  them span by span.
- US29.2 — As a Dev, I can tell which model, which service and endpoint, how often it's called, how
  slow it is, and whether it's failing, for the time window I'm already using everywhere else.
- US29.3 — As a Dev, when a call reports token usage or cost, I see it per call and summed across a
  window; when it does not, the screen says so plainly rather than showing a false zero.
- US29.4 — As a Dev, opening a trace that includes a model call, I can tell from the span itself,
  at a glance, that it was one.

## Business rules

- A span is a model call if and only if it carries at least one attribute whose key starts with
  `gen_ai.`. No list of known providers or model names decides this, now or later.
- A reading that was never sent on a span (no token or cost attribute present) is never summed as
  zero and never shown as zero — it is absent, the same convention every other screen in the product
  already follows for a missing reading.
- A span's model-call label is derived the same way wherever it appears — the new screen and the
  existing trace detail view compute it identically, so the same span is never a call in one place
  and not the other.

## Functional requirements

- FR29.1 — Identification is generic: a tenant-scoped query selects spans having any `span_attrs`
  key under the `gen_ai.` prefix, the same subcolumn-path technique `traces/queries.py` already uses
  for attribute filters, not a maintained enum of attribute names.
- FR29.2 — Grouping is by service name, span name (as the nearest thing to an endpoint a non-HTTP
  call has), and whatever value the model-name attribute holds *at query time* — read fresh on every
  query, never cached as a fixed list.
- FR29.3 — Count, p50/p95 latency and error rate per group are derived the same way the existing
  per-endpoint rollups already are, over the selected window.
- FR29.4 — Token and cost totals are summed only across spans that actually carry the relevant
  attribute. A span lacking it contributes to the group's call count and never to its sum.
- FR29.5 — The trace-detail badge (US29.4) is computed by the identical predicate as FR29.1, applied
  to the one span being viewed, so there is one rule for "is this a model call," read in two places.

## Non-functional requirements

- NFR29.1 — No schema migration, no new worker pipeline, no new ingestion door. This is a read path
  over the existing `spans` table; `apps/receiver` and `apps/workers` are unchanged.
- NFR29.2 — Tolerant of a `gen_ai.*` attribute name changing before the convention stabilizes.
  Because identification and grouping both key on "the field is present," not a specific name beyond
  the shared prefix, a future rename degrades to that field reading as absent until a small,
  config-level update — never a broken query, never a migration.
- NFR29.3 — Bounded by the same ClickHouse row/time caps every other API read already carries
  (`data/clickhouse/base.py`'s `READ_SETTINGS`) — no new exemption for this screen.

## Error cases

- A window with no model calls at all: the screen states plainly that no model calls were seen in
  the window — never a blank chart, never a spinner-forever, matching the house convention (F1's
  "all quiet" state).
- A span carrying a `gen_ai.*` attribute but no recognizable model-name attribute: grouped under an
  explicit "unlabeled model" bucket. Never silently dropped, never guessed at.
- ClickHouse unreachable: the same deferred-connection, 503-on-first-use behavior every other screen
  already has (`apps/api/src/nexovue_api/lifespan.py`). Nothing bespoke for this feature.

## Acceptance criteria

- AC29.1: The screen is reachable from the sidebar alongside the other telemetry-investigation
  screens, and respects the shared project and time-window selectors.
- AC29.2: A span with any `gen_ai.`-prefixed attribute appears in the aggregate list; a span with
  none does not, regardless of its service or span name.
- AC29.3: Each row shows service, span name, model name (or "unlabeled model"), call count, p50 and
  p95 latency, and error rate for the selected window.
- AC29.4: Token usage and cost are shown per row and summed only from calls that actually reported
  them; a row where no call reported either shows an em dash for those columns, never `0`.
- AC29.5: Opening a trace containing at least one model-call span shows a visible badge on that
  span in the existing trace detail view, computed by the same predicate as AC29.2.
- AC29.6: Renaming the attribute this feature currently reads for model name, in a fixture, causes
  the affected calls to move to the "unlabeled model" bucket rather than erroring, disappearing
  silently, or crashing the query.
- AC29.7: An empty window states explicitly that no model calls were seen, with the window's own
  bounds shown, never a blank page.
- AC29.8: A request to this screen is tenant-scoped through the same chokepoint
  (`data/clickhouse/base.py`) as every other telemetry read; the structural test that derives
  covered tables from the schema requires no exemption added for it.

## Dependencies and constraints

- Depends on nothing new in the ingestion path. `apps/receiver`, `apps/workers`, and the `spans`
  table's shape are unchanged by this feature.
- Constrained by `market-analysis-2026-09.md` §13's explicit recommendation against a frozen
  `gen_ai.*` metric contract — every functional requirement above is written to honor that rather
  than quietly reintroducing one.
- `data/clickhouse/base.py`'s tenancy chokepoint applies to every new statement this feature adds,
  exactly as it does to every existing one.

## Open decisions

None. The one real open question raised while scoping this — whether agent-trajectory views belong
in F29 — is resolved above, under *Out of scope*, with its reasoning, rather than left for design to
rediscover.
