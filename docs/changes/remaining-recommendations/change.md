# Change: remaining-recommendations — every remaining readiness recommendation, fixed or decided

## Classification

Security, Operations, Bug. Readiness group 9 (`docs/operations/production-readiness.md`,
*Recommended*), which the founder chose to fix in full (2026-10-10).

## Current behavior and problem

Read against the code on 2026-10-11 by four surveys; the findings, per item, are in `design.md`
(*Context and goal* and each item's "Today").

## Intended behavior

`design.md`, *Proposed design*: four parts — A security, B data safety, C reliability, D product and
console — each a step of the plan, with the founder decisions marked ⚑.

## Affected specifications and architecture

The super-admin specification (A3), a new short spec for personal-data export and deletion (D2),
ADR-014 (B3), `current-system-reference.md` (C2's correction and each part's behaviour), the readiness
document.

## Risks and rollback

Per part in `design.md`. Pre-deployment: `git revert` per step.

## Plan and validation

`plan.md`, after `design.md` is approved.

## Status

`status.md`.
