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
