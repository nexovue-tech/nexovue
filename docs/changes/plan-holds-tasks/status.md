# Status: plan-holds-tasks

## Lifecycle status

Implemented

## Current task

None.

## Completed

- Plan template merged with the task-plan template; task-plan template deleted.
- `task-plan` skill replaced by `write-plan`; `feature-work` gates merged; `change-work`, `CLAUDE.md`, the templates
  index and the workflow document updated.

## Next action

Use the single plan for F18 (`docs/features/synthetic-checks/plan.md`).

## Open decisions

None.

## Blocked

Nothing.

## Risks

A plan approved before its Current system section is genuinely read from the code would reintroduce the problem
task-plan-gate fixed. The template and the skill both require it; nothing enforces it mechanically.

## Validation

Searched `CLAUDE.md`, `AGENTS.md`, `docs/engineering/`, `docs/templates/` and `.claude/skills/` for "task-plan" and
"task plan". Two references remain, both deliberate. No tests cover workflow documents.
