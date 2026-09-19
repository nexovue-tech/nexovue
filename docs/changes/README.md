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
- [Explore skill](explore-skill/change.md) — the workflow had no step for an idea that is not yet a requirement.
- [Template gaps](template-gaps/change.md) — the four forms had no section for work every feature kept doing.
- [Task plan gate](task-plan-gate/change.md) — nothing sat between an approved plan and the first edited file.
- [Plan holds tasks](plan-holds-tasks/change.md) — the plan and its task plan were two documents and two approvals for one ordering.
- [QA-found defects](qa-found-defects/bug.md) — seven defects a hands-on pass found outside the feature it was testing, from a health check probing the wrong port to two chart colours that were one colour.
- [Compose dependencies](compose-dependencies/change.md) — every service now waits for what it needs at start, and is handed no connection setting it ignores.
- [Gate-found defects](gate-found-defects/bug.md) — a worker whose pipeline failed could never exit, a synthetics suite that judged a failing QA check, and test cleanups that filled development ClickHouse's memory with superseded parts.
- [Alert links to incident](alert-links-to-incident/bug.md) — an alert's link went to the check it was about, so it died with the check; it now opens the incident, which links on to the evidence while it exists.
- [Commit before response](commit-before-response/bug.md) — every API write answered before its transaction committed, so a screen re-reading on success could show the old rows; the session now commits before the response.
