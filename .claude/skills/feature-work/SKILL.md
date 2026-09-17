---
name: feature-work
description: >-
  Building or changing a customer-facing capability in nexovue through the
  spec → design → plan → implement lifecycle, with founder approval at each
  gate. Use when the founder names a product outcome or problem, asks to add
  or change a feature, or selects an entry from docs/product/feature-catalog.md.
  Not for bug fixes, refactors, migrations, or infrastructure work — use
  change-work for those.
---

# Feature work

Follow [`docs/engineering/workflow.md`](../../../docs/engineering/workflow.md). This skill is the
order of operations and the stop points; the standards it names own the rules themselves. Never
restate a standard here — read it.

## 1. Understand before proposing

- `git status --short`; preserve unrelated working-tree changes.
- Find the capability in `docs/product/feature-catalog.md` and read `docs/product/vision.md` for
  intent. Neither is evidence the capability exists.
- Read `docs/architecture/` for what is actually built, then read the code it names. **Code wins
  over any document.**
- Check `docs/features/<feature>/` and `docs/decisions/` for an existing spec or ADR.

Report what exists today before proposing anything. A capability the catalog calls unbuilt is often
partly built — the billing payment port, the RUM receiving path, and the synthetic-check pipeline
all existed before their catalog entries said so.

## 2. Specification — **gate**

Copy `docs/templates/spec.md` into `docs/features/<feature>/spec.md`. Acceptance criteria must be
testable and written as *must*, not as *does*.

**Stop. The founder approves the specification before any design work.**

## 3. Design — **gate**

Copy `docs/templates/design.md`. Name the affected services, the store changes, the tenancy path,
the failure and retry behavior, and the rejected alternative.

Any new decision that will be expensive to reverse gets an ADR in `docs/decisions/` — not a
paragraph buried in the design.

**Stop. The founder approves the design.**

## 4. Plan — **gate**

Write `docs/features/<feature>/plan.md` with `/write-plan`: ordered steps, and under each the tasks naming the
files that change and the tests that prove them — **read from the code, not the documents**. It lists what already
exists so tasks that are already done are deleted at their cheapest moment. Create `status.md` from
`docs/templates/status.md` and set the lifecycle state.

**Stop. The founder approves the plan before any file is edited.** A list of files is reviewable in a minute; the
code it describes is not.

## 5. Implement

Read the standards `AGENTS.md` routes you to for the paths you are touching, *before* writing code.
Then, whichever applies:

- **`apps/api`** — layout is `router.py` (HTTP only) → `service.py` → `queries.py` → `schemas.py`.
  Before writing a ClickHouse statement, read the predicate contract in
  `apps/api/src/nexovue_api/data/clickhouse/base.py`; `test_tenant_chokepoint_structure.py` and
  `test_postgres_tenancy_structure.py` parse your source, not your behavior. A mutating route needs
  an explicit role gate, not a bare signed-in dependency.
- **`apps/receiver`** — the gate chain order in `apps/receiver/internal/otlp/gates.go` is fixed. A response is
  written only after Kafka acknowledges.
- **`apps/workers`** — read `apps/workers/internal/pipeline/consume` before adding a pipeline. Mask first,
  derive before sampling, advance the watermark only after ClickHouse acknowledges, and park an
  undecodable message to the DLQ before its offset moves.
- **`apps/web`** — imports flow `shared → features → app`; a feature never imports another feature.
  Design tokens only. A missing reading is an em dash, never `0`.
- **Schema change** — copy `docs/templates/migration.md`. State the reasoning in the migration file
  itself.

Do not modify unrelated code. Do not rewrite working code the feature did not require.

## 6. Verify and hand off

Run `just check` and **read the output and the test counts** — exit zero alone is not evidence.
Run the proportionate integration, E2E, or chaos checks for the risks this change actually adds.

Then run `/review-diff` before reporting completion.

Fill in the plan's **Outcome**, tick its tasks, and update `status.md`: lifecycle state, what was validated and how, residual risks, specification and
documentation impact, and the next action.
