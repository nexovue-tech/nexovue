# ADR-039: An explanation is a frozen evidence record, with code choosing the cause

## Status

Accepted 2026-11-02 by the founder with the F3 design ([`design.md`](../features/ai-insights/design.md),
*Proposed ADRs*, the second); filed at implementation, 2026-11-09 (F3 plan Step 1). Extends ADR-022.

## Context

A finding says what changed and never why. The facts that would say where to look first (deploys,
calls to other services, new errors, checks, hosts) live on other screens and age out on their own
clocks: raw data by the project's raw retention, readings after 35 days. A summary written by a model
would be steered by any text an outsider can put into logs and traces, and would read as fluent
whether or not it was right. A browser key, printed in the customer's page, can move some telemetry.

## Decision

1. Every finding gets `finding_explanations` versions, written by the `evidence` job on
   `scheduler-light`, AI on or off. A version holds frozen facts with stable ids from their real
   identity (a deploy's id, an error group's id, a callee), a basis word for each, code-written
   sentences as segments (text, a name, a time), and the likely cause.
2. A new version is written only when an identity or a conclusion changed (FR43), so moved counts never
   make one. Reads of one finding never overlap.
3. `likely_cause` comes from fixed rules in `evidence/rules.py`, signed deploys first, or is null. A
   rule resting on words in telemetry (a version only seen in traffic, a new error group) never names a
   cause, only supports one. No model sets or changes it.
4. Three bases: `verified` (a signed record, or nexovue's own check), `telemetry` (counted from what
   was sent), `telemetry_text` (rests on words in it). Edges carry their door, so the dependency rules
   read the server door where a project has one (`service_edges_1m.door`, ClickHouse 000040).
5. Until the founder's accuracy bar is recorded and met, a stored cause is held: the API names none,
   and a non-production override exists so the founder can judge the rules (FR50, `CAUSE_BAR_RECORDED`).
6. What a model may be sent is a separate column, `model_input`, typed to numbers, closed lists and
   aliases; the real names behind the aliases are stored apart.

## Consequences

- The page explains a finding with AI off, from a record that outlives the raw data, and the rules run
  on every commit with no store.
- The rules know only what they were written to look for. A novel cause reads "Not enough evidence".
- One more Postgres row per changed read; storage is unmeasured.
- **Harder to reverse after the first customer:** the item shape inside `evidence`, and the rule names,
  which F5 keys on. A change then needs a reader that understands both.

## Rejected

A model finding the cause, agent-style (it needs tools and reads attacker text). Live links in place of
a frozen copy (the data ages out first). A numeric confidence (no data calibrates one). A table per fact
kind (a version is read whole, and the kinds will grow).
