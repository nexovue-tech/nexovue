# Task plan: <feature> — <phase>

> **One phase, not a whole feature.** Written when that phase starts, so a later phase is not
> planned in detail before an earlier one has taught anything.
>
> **Status:** Awaiting approval | Approved | In progress | Complete
>
> Produced by the `task-plan` skill. Approved by the founder before any file is edited.

## Current system

What exists today, **read from the code**, with `file:line`. Not from the design documents — they
describe intent, and this section exists to catch where intent and code disagree before that
disagreement becomes work.

## Change required

A pointer to the approved specification and design. **Never a restatement** — if this section
disagrees with them, one of the two is wrong and that is a finding, not a paragraph.

## Impact

| Area | What changes |
|---|---|
| Files | |
| Services | |
| Schema / migrations | |
| UI | |
| Tests | |

## Reuse

What already exists that this must not rebuild. Components, helpers, queries, formatters, test
fixtures. `docs/design/principles.md` rule 4 applied to this phase specifically.

## Gaps

What is genuinely missing and has to be created. A gap that turns out to already exist belongs in
**Current system** as a correction, not here.

## Tasks

Ordered. Each names the file it touches and the test that proves it. Dependencies are expressed by
position — a task that must come second is second.

- [ ] **T1** — `path/to/file` · what changes · proved by `test`
- [ ] **T2** —

## New risks

Only what this analysis found. The feature's standing risks live in `status.md`; do not copy them.

## Questions

Blocking questions stop the phase. Non-blocking ones are recorded with an assumption and proceed.

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|

## Validation

What runs when this phase is done, and what evidence counts.

## Outcome

Filled in when the phase completes: what was actually built, what changed from this plan and why,
and what the next phase inherits. A task plan that ends without this is a proposal, not a record.
