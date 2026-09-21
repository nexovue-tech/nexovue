# Status: catalogue-audited

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- Every roadmap claim checked against code. Two rows corrected, four confirmed accurate and left
  alone, Track 1 marked complete, F37 and the blocker count brought up to date.
- The trace-list wait fixed, with the reason recorded at the helper.

## Next action

Founder: review the diff and say **push**. Then **F37's specification** is the natural next gate —
it is the only Phase 1 feature without one, and Track 3 is now the critical path.

## Open decisions

- **F37's scope.** It is both a feature and the deployment track's first bullet. Whether it is
  specified as one feature or split — the installable artefact, and the deployment-mode concept that
  blockers 9 and 10 describe — is a decision the specification gate should make explicitly.

## Blocked

Nothing.

## Risks

- **An audit is a photograph.** Four documents were stale within days of being written, and nothing
  here prevents a fifth. A structural test could hold *some* of it — a catalogue row claiming
  `🔴 No` beside a note saying "Built" is machine-checkable — and is not written.

## Validation

- `just check`, **exit 0 twice in succession**: **4144 Python passed, 2 skipped**; **1335 web**
  (123 files); **120** browser SDK; Go, format, lint, `mypy` (575 files) and the contract checks
  clean. Twice on purpose — the defect this change fixed appears only under load, so one green run
  is not evidence.
- **Every corrected claim was checked against the thing that proves it**: `admin/` and
  `features/admin/` for F31; `apitokens/` and `assistant/` for F36; the absence of a findings
  producer in `apps/workers` for F1; no `dashboards` feature for F20; no `assume_role` anywhere for
  F24; `organizations/router.py` for F27; the `deploys` router for F28.
- **The remaining blockers were re-checked**, not re-read: no `.github`, no alembic step in either
  deployment file, no deployment-mode concept anywhere, and three scheduler services with no
  healthcheck.

## Specification impact

None.

## Documentation impact

`docs/product/feature-catalog.md`.

## Last updated

2026-09-21
