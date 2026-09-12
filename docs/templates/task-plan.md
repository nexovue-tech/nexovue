# Task plan: <feature or change>

> **Status:** Awaiting approval | Approved | In progress | Complete
>
> Produced by the `task-plan` skill from an approved `plan.md`. **The founder approves it before any
> file is edited** — a list of files is reviewable in a minute; the code it describes is not.

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
fixtures. `docs/design/principles.md` rule 4 applied to this work specifically.

## Gaps

What is genuinely missing and has to be created. A gap that turns out to already exist belongs in
**Current system** as a correction, not here.

## Tasks

Ordered, and the order is the plan's. Each names the file it touches and the test that proves it.
Dependencies are expressed by position — a task that must come second is second.

Strike a task that the code reading showed is already done, and say so. Work deleted here is work
saved at its cheapest moment.

- [ ] **T1** — `path/to/file` · what changes · proved by `test`
- [ ] **T2** —

## New risks

Only what this analysis found. The standing risks live in `status.md`; do not copy them.

## Questions

Blocking questions stop the work. Non-blocking ones are recorded with an assumption and proceed.

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|

## Validation

What runs when this is done, and what evidence counts.

## Outcome

Filled in when the work completes: what was actually built, what changed from this plan and why.
A task plan that ends without this is a proposal, not a record. The difference between Tasks and
Outcome is the most honest estimate anyone will have of how well this planning works.
