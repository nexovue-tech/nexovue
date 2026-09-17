# Plan: <feature or change>

> **Status:** Awaiting approval | Approved | In progress | Complete
>
> **One document: the order of the work and its tasks.** Steps say what ships and in what order; tasks under
> each step name the file that changes and the test that proves it, read from the code. **The founder approves
> this plan before any file is edited** — a list of files is reviewable in a minute; the code it describes is
> not.

## Preconditions

What must be approved or in place before this plan starts.

## Current system

What exists today, **read from the code**, with `file:line`. Not from the design documents — they describe
intent, and this section exists to catch where intent and code disagree before that disagreement becomes work.

## Change required

A pointer to the approved specification and design. **Never a restatement** — if this section disagrees with
them, one of the two is wrong and that is a finding, not a paragraph.

## Impact

| Area | What changes |
|---|---|
| Files | |
| Services | |
| Schema / migrations | |
| UI | |
| Tests | |

## Reuse

What already exists that this must not rebuild: components, helpers, queries, formatters, test fixtures.
`docs/design/principles.md` rule 4 applied to this work specifically.

## Gaps

What is genuinely missing and has to be created. A gap that turns out to already exist belongs in **Current
system** as a correction, not here.

## Steps and tasks

Ordered so that **the first step ships something judgeable**. A plan whose first result arrives at the end
proves its assumptions where they are most expensive to be wrong — `ADR-0019` settled this for the repository:
one thin end-to-end path first, then widen.

Each step states what ships and how the founder can see it working, then lists its tasks. **A task names a file
and a test** — "rebuild the services screen" is a step, not a task. Dependencies are expressed by position.
Strike a task the code reading showed is already done, and say so: work deleted here is work saved at its
cheapest moment.

### Step 1 — <what ships>

*Judgeable:* <what the founder does to see it working>

- [ ] **T1** — `path/to/file` · what changes · proved by `test`
- [ ] **T2** —

## New risks

Only what this analysis found. Standing risks live in `status.md`; do not copy them.

## Questions

A blocking question stops the work. A non-blocking one is recorded with an assumption and proceeds. Say where
each answer will live — `spec.md` open decisions or an ADR, never this document.

| # | Question | Blocking | Where the answer will live |
|---|---|---|---|

## Gates

- [ ] Specification approval
- [ ] Design approval
- [ ] **Plan approval — before the first edited file**
- [ ] Implementation
- [ ] Tests
- [ ] Review
- [ ] Design QA — the built screen against the approved design, for customer-facing work
- [ ] Production-readiness assessment
- [ ] Documentation and status update

## Validation plan

Which checks run, and when. Read output and counts; an exit code is not evidence.

## Rollout and rollback

Name the real risk. Pre-deployment, it is rarely the rollout — it is more often the first step shipping and
nobody judging it.

## Outcome

Filled in when the work completes: what was actually built, and what changed from this plan and why. A plan that
ends without this is a proposal, not a record. The difference between the tasks and the outcome is the most honest
estimate anyone will have of how well this planning works.
