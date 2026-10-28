---
name: write-plan
description: >-
  Writing the plan for an approved feature or change — ordered steps and, under
  each, tasks naming the files that change and the tests that prove them, read
  from the code — for the founder's approval before any file is edited. Use when
  a design or change record is approved and implementation is next, when the founder asks
  what exactly will change, or when feature-work or change-work reaches the plan.
  Not for deciding what to build — that is the specification and design gates,
  already passed by the time this runs.
---

# Write the plan

The gate between an approved design and the first edited file. It produces
`docs/features/<feature>/plan.md` — or `docs/changes/<id>/plan.md` — from `docs/templates/plan.md`, and
**the founder approves it before anything is written.**

One document holds both the order of the work and its tasks. There is no separate task plan.

## 1. Read what was already decided

The feature's `spec.md`, `design.md`, any wireframes, and `status.md`. You are not re-deciding any of it. If
the work seems to need something the specification does not cover, that is a **question**, not a licence.

## 2. Read the code, not the documents about the code

This is the section that earns the gate. Open the files this work will touch and read them.

**Do not pattern-match a schema or a signature.** A grep anchored to indentation silently skips every field
declared after a comment line, and this codebase comments between fields constantly. That exact mistake put a
non-existent backend gap into five documents and four commits before reading one file corrected it.

Record what you find in **Current system** with `file:line`. Where the code disagrees with an approved document,
say so — that disagreement is the most valuable thing this step produces, and it is cheapest now.

## 3. Check what is already built

Before listing a task, check whether it exists. Two of the console redesign's first tasks turned out to be
already implemented. Every task deleted at this stage is work saved at its cheapest possible moment.

## 4. Check versions and libraries, narrowly

If the work touches a library, a pinned version or an external API, confirm it has not changed under you, and
resolve any library choice the design left for research. Ten minutes, not a scope review.

**Do not research scope here.** "Is this the right feature" was settled at the specification gate. If the analysis
shows the specification was wrong, say so and go back to it.

## 5. Order the steps, then write the tasks

- **Order steps so the first one ships something judgeable** — one thin end-to-end path first, then widen
  (`ADR-0019`). State for each step how the founder can see it working.
- **Tasks name a file and a test.** "Rebuild the services screen" is a step, not a task.
  "`shared/components/Sparkline.tsx` — shared-window sparkline, proved by `Sparkline.test.ts`" is a task.
- **Questions route.** A blocking question stops the work; its answer belongs in `spec.md` or an ADR, never in the
  plan. Record where the answer will live.

Fill every section of the template, and create `status.md` from `docs/templates/status.md` if it does not exist.

## 6. Stop

**The founder approves before any file is edited.** The list of files is reviewable in a minute; the code it
describes is not.

## 7. After the work

Fill in **Outcome** — what was actually built, and what changed from the plan and why — tick the tasks, and update
`status.md`. The plan stays in the repository as the record of how the work was built.
