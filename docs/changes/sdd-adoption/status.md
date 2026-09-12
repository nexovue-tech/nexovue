# Status: SDD adoption

## Lifecycle status

Implemented

## Current task

None.

## Completed

- Repository baseline completed.
- `AGENTS.md` made agent-agnostic and populated with repository-specific rules.
- Product vision, feature catalog, and research moved to their authoritative homes.
- Minimal standards, current architecture docs, templates, ADRs, and production-readiness baseline added.
- Root README is now concise; the former source-derived content is preserved at `docs/architecture/current-system-reference.md`.
- Focused current-state architecture, engineering, quality-gate, and operations documents now distinguish verified behavior from production gaps.
- Product feature catalog now contains a verified high-level capability baseline and includes account/project access and telemetry intake/data protection, which were previously missing as catalog entries.
- Each important existing capability in that baseline has a code-sourced current-state `spec.md` under `docs/features/`.
- Documentation verified against code: every markdown link and backticked file reference resolves; documented `just` recipes, ports, lint/type/test tooling, and cited routers, probes, pipelines, migrations, and screens all exist as described.
- One lifecycle vocabulary now applies everywhere: the templates use the `AGENTS.md` states rather than a second, conflicting list.
- `docs/decisions/`, `docs/changes/`, and `docs/templates/` have indexes, and README and `AGENTS.md` link them; the feature-spec template is named `spec.md` to match the file agents are told to create.

## Next action

Create a separate CI/release-workflow change only after selecting the CI provider and release policy. For product work, select the first feature in `docs/product/feature-catalog.md` and draft its `spec.md`.

## Blocked

No technical blocker. CI provider/release policy is a founder decision.

## Validation

Documentation: every markdown link and backticked file reference across the set resolves, and the
documented `just` recipes, local ports, lint/type/test tooling, and cited routers, probes, pipelines,
migrations and screens were each checked against the code.

`just check` on 2026-09-12, exit code 0, output read: 2277 Python, 120 browser-SDK, and 613 web
tests passed, plus `buf lint` and `buf breaking`.

The first run of this gate failed, on a pre-existing signup defect unrelated to the documentation.
It is fixed and recorded separately under `docs/changes/signup-session-before-commit/`.

## Last updated

2026-09-12
