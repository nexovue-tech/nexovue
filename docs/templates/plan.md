# Plan: <name>

## Preconditions

What must be approved or in place before this plan starts.

## Phases

Ordered, and each one **ships something judgeable**. A plan whose first result arrives at the end
proves its assumptions where they are most expensive to be wrong — `ADR-0019` settled this for the
repository: one thin end-to-end path first, then widen.

Expand a phase into named files and tests **when that phase starts**, not all of them upfront —
see [task-plan.md](task-plan.md). A task list for a later phase, written before the first has taught
anything, is fiction with checkboxes.

### Phase 1 — <the thin path>

- [ ]

### Phase 2 — <widen>

- [ ]

## Ordered gates

- [ ] Specification approval
- [ ] Design approval
- [ ] Plan approval
- [ ] Task plan approval — once per phase, before its first edit
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design, for customer-facing work
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

Which checks run, and after which phase. Read output and counts; an exit code is not evidence.

## Rollout and rollback

Name the real risk. Pre-deployment, it is rarely the rollout — it is more often a phase shipping
and nobody judging it.
