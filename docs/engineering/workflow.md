# Development workflow

The lifecycle every change follows, from a stated outcome to a recorded status. This document is
agent-agnostic: Claude Code skills, other agents, and human contributors all follow it. `AGENTS.md`
holds the permanent working rules and points here for the procedure.

## Starting or resuming work

1. Inspect `git status --short`; preserve unrelated user changes.
2. Classify the request: feature, bug, refactor, migration, infrastructure, security, or operations
   change.
3. Locate or create the smallest appropriate workspace under `docs/features/` or `docs/changes/`,
   using the forms in `docs/templates/` (see `../templates/README.md`).
4. Read its status and plan, then inspect the affected implementation and tests.
5. State assumptions, specification impact, and important tradeoffs before making a material
   architecture or product decision. Do not invent business requirements.

Report the selected classification, the documents involved, the next approval gate, and the
immediate next action before substantive work begins. A question or read-only review starts no
workflow; say so rather than opening a workspace for it.

If a requirement, acceptance criterion, or approval boundary is materially ambiguous, stop before
implementation, explain the ambiguity and its impact, and request founder direction. Do not turn an
assumption into product behavior silently.

## Proportionate workflow

- **Small, low-risk change:** short change record, implementation, focused tests, review.
- **Medium feature:** feature spec, approved design, implementation, tests, review.
- **Major feature or material migration/security/infrastructure change:** spec → approval → design →
  approval → plan → approval → implementation → tests → code review → production-readiness
  assessment → staging/rollout approval → documentation/status update.

Every feature and change status moves through `Proposed → Approved → In development → Implemented →
Verified → Released` as applicable. Small fixes may use a change record, but must include a
regression test when practical.

## Founder workflow

The founder starts work by naming the product outcome or problem, not by prescribing code. The agent
then identifies the relevant product/catalog entry and the smallest suitable workspace.

| Founder request | Workflow | Founder approval needed |
|---|---|---|
| Small bug or low-risk change | Create/update a short `docs/changes/<id>/` record, reproduce or inspect, plan the fix, implement, test, self-review, and update status. | Only if requirements, risk, or scope are unclear/material. |
| Medium feature | Create `docs/features/<feature>/spec.md`, derive testable acceptance criteria, design the change, then implement and verify. | Approve specification and design before implementation. |
| Major feature, migration, security, or infrastructure change | Create spec, design, plan, status, review and readiness evidence. | Approve specification, design, plan, and production/staging rollout. |
| Production incident or customer feedback | Create a change/bug record, investigate and reproduce, identify root cause and specification impact, add regression coverage, then release through the appropriate gates. | Approve material behavior, risk, or release decisions. |

## Handoff

At each handoff, report what is complete, what was verified and how, risks and assumptions,
documentation and specification impact, and the next action. The founder can approve, redirect
scope, or stop work.

Chat history is never the source of truth: the relevant `status.md` is. A handoff that is not
written into `status.md` did not happen.

## Quality gates

- [Implementation review](../quality/code-review.md) before reporting completion.
- [Quality gates](../quality/quality-gates.md) for review, validation, and release expectations.
- [Production-readiness gate](../quality/production-readiness.md) before any production rollout.
