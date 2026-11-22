# Testing standard

`just check` is the repository's offline pre-commit gate: formatting, Go/Python/TypeScript lint and type checks, unit tests, and protobuf compatibility checking.

- Go tests run per module and always use `-count=1`; cached success is not evidence.
- Python uses pytest with `asyncio_mode=auto` and strict mypy. Live suites fail when dependencies are unreachable unless the explicit local opt-out is used.
- TypeScript uses Vitest; browser end-to-end tests use Playwright against a running stack.
- `just e2e`, `just chaos`, and `just load` are release-risk checks, not replacements for unit tests.
- A live test shares the stack with that stack's own workers and sweeps, which read the real clock and every organization's rows. Read a test's clock when the test starts (a fixture), never once at import; keep the rows it writes out of those workers' reach; and a test that runs a cross-organization sweep itself sweeps only just past its own rows, never other tests' rows in flight ([wording settles once](../changes/wording-settles-once/bug.md)). A test that commits jobs holds them from those workers (`tests/helpers/held_jobs.py`); a test that runs the scheduler's real workers itself, so its own workers must claim its jobs, uses a database of its own on the stack's server (`tests/helpers/private_database.py`); and a test that needs an install the stack is not (AI never set up, say) carries a marker `pyproject.toml` deselects, and the CI job that provides that install selects it.
- Every xdist worker collects every test file and keeps what collection built — module globals and every parameter — for the whole run. So a parameter names or shares its data rather than carrying its own copy, and anything bulky a file's tests share (parsed source, generated series) comes from a module-scoped fixture, which only the worker running that file builds and which is released when the file ends ([test workers hold memory](../changes/test-workers-hold-memory/bug.md)).
- Add or update regression coverage for meaningful behavioral changes, failure paths, tenant boundaries, migrations, and data-delivery guarantees.

Record validation actually run in a feature/change `status.md`; do not describe unrun checks as passing.

Read test output and counts. An exit code alone is not evidence that the intended tests executed.
