# Document templates

Copy these into a feature or change workspace; do not edit them in place.

| Template | Copy to | Purpose |
|---|---|---|
| [spec.md](spec.md) | `docs/features/<feature>/spec.md` | What a feature must do, and its acceptance criteria. |
| [design.md](design.md) | `docs/features/<feature>/design.md` or `docs/changes/<id>/design.md` | How it will be built, and the tradeoffs. |
| [plan.md](plan.md) | alongside the spec or change record | The ordered steps to get there. |
| [task-plan.md](task-plan.md) | beside the plan, as `task-plan.md` | The plan expanded into named files and tests, approved before any edit. |
| [status.md](status.md) | every feature and change workspace | Current lifecycle state, validation, and next action. |
| [change.md](change.md) | `docs/changes/<id>/change.md` | A non-feature change: fix, refactor, migration, infrastructure. |
| [bug.md](bug.md) | `docs/changes/<id>/bug.md` | A reported defect, its reproduction, and its root cause. |
| [migration.md](migration.md) | alongside the change record | A schema or data migration and its rollback. |

`status.md` is the source of truth for where work stands. Chat history is not.
