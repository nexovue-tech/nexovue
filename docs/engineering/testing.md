# Testing standard

`just check` is the repository's offline pre-commit gate: formatting, Go/Python/TypeScript lint and type checks, unit tests, and protobuf compatibility checking.

- Go tests run per module and always use `-count=1`; cached success is not evidence.
- Python uses pytest with `asyncio_mode=auto` and strict mypy. Live suites fail when dependencies are unreachable unless the explicit local opt-out is used.
- TypeScript uses Vitest; browser end-to-end tests use Playwright against a running stack.
- `just e2e`, `just chaos`, and `just load` are release-risk checks, not replacements for unit tests.
- Add or update regression coverage for meaningful behavioral changes, failure paths, tenant boundaries, migrations, and data-delivery guarantees.

Record validation actually run in a feature/change `status.md`; do not describe unrun checks as passing.

Read test output and counts. An exit code alone is not evidence that the intended tests executed.
