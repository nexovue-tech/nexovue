---
name: change-work
description: >-
  Fixing a bug or making a non-feature change in nexovue — refactor, migration,
  performance, security, infrastructure, or operations — through a change record
  rather than a feature spec. Use when something is broken, a test fails
  intermittently, a standard has gone stale, or a change adds no new
  customer-facing behavior. Use feature-work instead for new capabilities.
---

# Change work

Follow [`docs/engineering/workflow.md`](../../../docs/engineering/workflow.md). This skill is the
order of operations; the standards it names own the rules themselves.

## 1. Open the workspace

`git status --short` first; preserve unrelated working-tree changes.

Create `docs/changes/<id>/` from the templates:

- A defect → `docs/templates/bug.md` as `bug.md`.
- Anything else → `docs/templates/change.md` as `change.md`.
- Always `status.md`. Add `plan.md` or `design.md` only if the change is large enough to need one.

Then add the record to the list in `docs/changes/README.md`.

## 2. Reproduce before diagnosing

A defect is not understood until it has been reproduced. Write the reproduction into the record —
the exact steps and the decisive output, not a summary of it.

Put throwaway reproduction scripts in the scratchpad directory, never in the repository.

**Rule out causes with evidence, not reasoning.** Record what you ruled out and the measurement that
ruled it out. The signup-session defect was found this way: Redis eviction, session TTL, and the
`sessions_valid_from` epoch were each excluded by a reading, which is what left the commit ordering
as the only remaining explanation.

Intermittent under load and clean in isolation is a *timing* signature — look for a window between
two writes to different stores.

## 3. Find the root cause, not the symptom

State the root cause in the record in one paragraph, naming the file and the ordering or condition
that is wrong.

Do not patch around it. If the root correction is larger than the reported symptom or changes scope,
say so and let the founder decide the scope — do not silently narrow the fix or silently widen it.

## 4. State customer impact honestly

Nexovue is pre-deployment, so no customer is affected today. That is not the same as "test-only".
Write what *would* happen to a real customer, and what the same defect would cost after the first
one exists.

## 5. Fix, then prove the fix

- Implement the root correction. Read the standards `AGENTS.md` routes you to for the paths you
  touch.
- Delete what the change replaces — superseded code, config, docs, or database objects — in the same
  change. State anything that must remain, and why.
- Add regression coverage and **verify it in both directions**: confirm the test fails without the
  fix and passes with it. Record the observed failure output. A regression test never seen red is
  not evidence.
- If the defect exposed a stale or missing standard, propose the update as part of this change.

## 6. Verify and hand off

Run `just check` and read the output and test counts. If the change altered ingest durability,
tenancy, or a store write path, run the proportionate live, E2E, or chaos check too.

Then run `/review-diff` before reporting completion.

Update `status.md`: lifecycle state, what was validated and how, residual risks, documentation
impact, and the next action.
