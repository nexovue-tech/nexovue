# Quality gates

## Current automated gate

`just check` runs formatting, linting, type checking, unit tests, and protobuf compatibility checks without Docker. This is the required offline gate before handoff.

## Required review questions

For a changed feature or change record, verify:

1. The implementation meets approved requirements and acceptance criteria.
2. Architecture boundaries, tenancy, security, and repository standards are preserved.
3. Tests cover success, failure, and regression behavior proportionate to risk.
4. New configuration, migration, observability, dependency, performance, and rollback implications are addressed.
5. Documentation/status reflect actual behavior and validation—not hoped-for results.

Run E2E, live-stack, load, chaos, and migration/backup checks when the changed risk requires them. They are not all part of the offline gate.

**A suite that spends a metered third-party quota is not part of `just check`.** Most live suites
cost nothing to repeat — ClickHouse and Postgres are ours. A suite that calls a paid provider is
different: every run is spent, and a gate that runs on every change spends it on every change. The
mail suite sent two real messages per run and consumed most of a month's allowance in one working
session before anyone noticed, because nothing in the gate was counting.

Such a suite calls `require_opt_in` (`tests/helpers/live.py`) and runs under `just test-billable` —
before a release, or when the thing it proves is what changed. The skip is loud and names the cost,
so it is never mistaken for a pass; asked for, it still fails rather than skipping when it cannot
run.
