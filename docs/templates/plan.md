# Plan: <name>

## Preconditions

What must be approved or in place before this plan starts.

## Ordered implementation steps

Ordered so that **the first steps ship something judgeable**. A plan whose first result arrives at
the end proves its assumptions where they are most expensive to be wrong — `ADR-0019` settled this
for the repository: one thin end-to-end path first, then widen.

The steps here are the shape of the work. `task-plan.md` expands them into named files and named
tests, once, before the first edit.

- [ ]

## Gates

- [ ] Specification approval
- [ ] Design approval
- [ ] Plan approval
- [ ] Task plan approval — before the first edited file
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design, for customer-facing work
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

Which checks run, and when. Read output and counts; an exit code is not evidence.

## Rollout and rollback

Name the real risk. Pre-deployment, it is rarely the rollout — it is more often the first step
shipping and nobody judging it.
