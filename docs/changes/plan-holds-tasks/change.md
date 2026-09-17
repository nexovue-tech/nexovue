# Change: plan-holds-tasks — the plan and its tasks become one document

## Classification

Refactor — of the engineering workflow, not of code.

## Current behavior and problem

Since [task-plan-gate](../task-plan-gate/change.md), approved work produced two documents in sequence: `plan.md`,
an ordering of steps, and then `task-plan.md`, the same order expanded into files and tests, each with its own
template, its own skill (`task-plan`) and its own approval gate in `feature-work`.

The founder asked, on 2026-09-17, for one document instead. Two consecutive documents for one ordering meant two
approvals, two places to update when work changed, and a plan that was approved before anyone had read the code
it would change.

## Intended behavior

- **`plan.md` holds the steps and, under each step, its tasks** — named files and named tests, read from the code.
- **One gate:** the founder approves the plan before the first file is edited. What the task-plan gate protected
  is kept: the code is read before approval, and nothing is edited until then.
- Everything the task-plan template asked for moves into the plan template: Current system with `file:line`,
  Impact, Reuse, Gaps, New risks, Questions with where their answers live, and Outcome.

## Affected specifications and architecture

| File | Change |
|---|---|
| `docs/templates/plan.md` | Merged with the task-plan template; steps now contain tasks |
| `docs/templates/task-plan.md` | Deleted |
| `docs/templates/README.md` | One row for the plan |
| `docs/engineering/workflow.md` | "Before code" describes the single plan; notes the earlier practice |
| `.claude/skills/task-plan/` | Replaced by `.claude/skills/write-plan/` |
| `.claude/skills/feature-work/SKILL.md` | Plan and task-plan gates merged into one; steps renumbered |
| `.claude/skills/change-work/SKILL.md` | A `plan.md` holds its tasks, written with `/write-plan` |
| `CLAUDE.md` | Skill table |

**Kept as records:** `docs/features/console-redesign/task-plan.md` and `docs/changes/task-plan-gate/` describe how
that work was actually done and are not rewritten. `AGENTS.md` never named the task plan and needs no change.

## Risks and rollback

The risk is the one task-plan-gate was created to remove: a plan approved as an ordering, then edited from before
the code was read. The single plan closes it by requiring Current system and the tasks to be written, from the
code, before the one approval. Rollback is restoring the deleted template and skill from git history.

## Plan and validation

Validated by searching the live workflow documents, templates, skills and `CLAUDE.md` for any remaining reference to
a separate task plan. The two that remain are deliberate: the workflow's note on earlier practice, and the new skill
stating that no separate task plan exists. First use: F18's plan.

## Status

See [`status.md`](status.md).
