# Feature: <name>

## Status

Proposed | Approved | In development | Implemented | Verified | Released

Use the lifecycle defined in `AGENTS.md`. Approval gates are recorded in this feature's
`status.md`, not as extra status values here.

> **Delete any section that does not apply.** A heading answered "N/A" reads as considered when it
> was not, which is worse than one that was never asked.

## Problem

What is wrong today, and for whom. Not the solution, and not the goal — the thing that hurts.

## Goal

What is true once this is solved.

## Target users

Who this serves, from `docs/product/vision.md`, and which surfaces each touches. **Name any persona
this feature does not serve**, so scope does not later widen to a user with nowhere to stand.

## Scope

## Out of scope

## User requirements

What a user must be able to do, in their words. *"A customer can see which of their services is
worst right now."*

## Business rules

Invariants that hold regardless of screen or endpoint. *"A customer cannot hold two active
subscriptions for one product."* These constrain every path rather than describing one, which is
what separates them from the requirements above and below.

## Functional requirements

What the system must do, in the system's words — the behavior behind the user requirements.
*"Ranking is by absolute error count, so 50% of 96 requests ranks below 4% of 12,000."*

If a line here only restates a user requirement, delete it. Two sections saying the same thing will
disagree within a month.

## Non-functional requirements

## Error cases

What can go wrong **from the user's side**, and what the product owes them in each case. A product
decision, not an implementation one: *"a node that stopped reporting renders as unknown, never as
zero"* is decided here; how the request is retried is decided in `design.md`.

For each: what the user sees, whether they can recover, and whether we can tell the difference
between this failure and a normal empty result.

## Acceptance criteria

Testable, and written as *must*, never as *does*. Cover the error cases above, not only the happy
path.

## Dependencies and constraints

Including any recorded decision this feature contradicts, and where it is recorded.

## Open decisions
