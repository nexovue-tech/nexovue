# Change records

A change record is the workspace for work that is not a product feature: bug fixes, refactors, migrations, infrastructure, security, and engineering-system changes. Each lives in `docs/changes/<id>/` and holds at least a `change.md` and a `status.md`, plus `plan.md` and `design.md` when the change is large enough to need them.

Start from the [templates](../templates/README.md). Feature work belongs under [`docs/features/`](../features/README.md) instead.

## Records

- [SDD adoption](sdd-adoption/change.md) — documentation structure, agent instructions, templates, and the current-state baseline.
- [Signup session before commit](signup-session-before-commit/bug.md) — signup minted a session before the tenant it named was durable.
- [Agent skills](agent-skills/change.md) — the development workflow moved to an agent-agnostic document, and four Claude Code skills that run it.
- [Design system restore](design-system-restore/change.md) — the design system specification was deleted while four references to it survived in shipped code.
- [Decision log](decision-log/change.md) — an index for the 112 decision records deleted in `e0f11a0`, and the contradiction one of them exposes.
- [Counter overcount record](counter-overcount-record/change.md) — the architecture reference claimed a measured span overcount does not happen.
