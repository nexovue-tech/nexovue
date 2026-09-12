---
name: task-plan
description: >-
  Expanding an approved plan into named files, named tests and an order,
  before any code is written. Use when a feature or change has an approved
  plan.md and implementation is about to start, when Raj asks what exactly
  will change, or when feature-work or change-work reaches implementation.
  Not for deciding what to build — that is the specification and design
  gates, already passed by the time this runs.
---

# Task plan

The gate between an approved plan and the first edited file. It produces
`docs/features/<feature>/task-plan.md` — or `docs/changes/<id>/task-plan.md` — from
`docs/templates/task-plan.md`, and **the founder approves it before anything is written.**

One task plan per feature or change. `plan.md` already ordered the work so its first steps ship
something judgeable; this expands that order into files and tests without re-deciding it.

## 1. Read what was already decided

The feature's `spec.md`, `design.md`, any wireframes, `plan.md`, and `status.md`. You are not
re-deciding any of it. If the work seems to need something the specification does not cover, that
is a **question**, not a licence.

## 2. Read the code, not the documents about the code

This is the section that earns the gate. Open the files this work will touch and read them.

**Do not pattern-match a schema or a signature.** A grep anchored to indentation silently skips
every field declared after a comment line, and this codebase comments between fields constantly.
That exact mistake put a non-existent backend gap into five documents and four commits before
reading one file corrected it.

Record what you find with `file:line`. Where the code disagrees with an approved document, say so
here — that disagreement is the most valuable thing this step produces, and it is cheapest now.

## 3. Check what is already built

Before listing a task, check whether it exists. Two of the console redesign's first tasks turned out
to be already implemented. Every task you delete at this stage is work saved at its cheapest
possible moment.

## 4. Check versions, narrowly

If the work touches a library, a pinned version, or an external API, confirm it has not changed
under you. Ten minutes.

**Do not research scope here.** "Is this the right feature" was settled at the specification gate.
Widening scope at the task gate is scope creep with a process blessing; if the analysis shows the
specification was wrong, say so and go back to it.

## 5. Write the plan

Fill every section of the template. Two rules:

- **Tasks name a file and a test.** "Rebuild the services screen" is a step, not a task.
  "`shared/components/Sparkline.tsx` — shared-window sparkline, proved by `Sparkline.test.ts`" is a
  task.
- **Questions route.** A blocking question stops the work; its answer belongs in `spec.md` Open
  decisions or an ADR, never in this document. Record where the answer will live, so the decision is
  not made here by default.

## 6. Stop

**The founder approves before any file is edited.** That is the entire point of the gate: the list
of files is reviewable in a minute, and the code it describes is not.

## 7. After the work

Fill in **Outcome**: what was actually built, and what changed from the plan and why. Tick the steps
in `plan.md` and update `status.md`.

The task plan stays in the repository. It is the record of how the work was actually built, and the
difference between its Tasks and its Outcome is the most honest estimate anyone will have of how
well this planning works.
