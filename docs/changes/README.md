# Change records

A change record is the workspace for work that is not a product feature: bug fixes, refactors, migrations, infrastructure, security, and engineering-system changes. Each lives in `docs/changes/<id>/` and holds at least a `change.md` and a `status.md`, plus `plan.md` and `design.md` when the change is large enough to need them.

Start from the [templates](../templates/README.md). Feature work belongs under [`docs/features/`](../features/README.md) instead.

## Records

- [SDD adoption](sdd-adoption/change.md) — documentation structure, agent instructions, templates, and the current-state baseline.
- [Signup session before commit](signup-session-before-commit/bug.md) — signup minted a session before the tenant it named was durable.
