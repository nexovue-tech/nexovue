# Status: Setup-wait sampler

## Lifecycle status

Implemented, in review.

## Current task

None.

## Completed

- Reproduced the CI failure under load (1 of 30), and found the exact statements in the window with a
  statement log.
- Showed with a concurrent index build that the window cannot deadlock one, and that the control case
  (blocked in `pg_advisory_lock`) does.
- Fixed the test's sampler. Added three regression tests. The deterministic one was seen failing
  against the original sampler and passing with the fix.
- `/review-diff`: every changed file is explained by this record. The tenancy, authorization,
  durability, masking, migration and web checks do not apply, because only a test and docs changed.
  One LOW finding, fixed: the stand-in waiters' poll interval was a bare `0.5`, and is now
  `LOCK_POLL_SECONDS`. No production code changed.

## Next action

The coordinator reviews the diff on `wt/flake` and decides whether to commit it to
`feature/phase-2`.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- The sampler knows the wait has started when a statement contains `advisory_lock`. If `env.py`
  stopped using an advisory lock, the test would fail loudly after 60 s ("never seen waiting"). It
  would not pass quietly.
- A setup that crashes before asking for the lock now takes up to 60 s to fail this test, instead of
  4 s. Its non-zero exit is still reported.
- 0 failures in 60 loaded runs after the fix is weak evidence on its own: at the 1-in-30 rate seen
  before, about a 13% chance. The deterministic regression test is the evidence.

## Validation

- Load reproduction before the fix: `python3 load.py 6 5 4` gave `runs 30 fails 1`, with the CI
  assertion.
- After the fix, the same load for 10 rounds: `runs 60 fails 0`, and all 60 logs say `1 passed`.
- `uv run pytest apps/api/tests/test_setup_live.py apps/api/tests/test_migration_rules.py`: 31 passed.
- `uv run mypy .`: no issues in 777 source files. `uv run ruff check .`: all checks passed.
  `uv run ruff format --check .`: 1136 files already formatted.
- `just check` was not run, at the coordinator's instruction. CI on the branch is the next full gate.

## Specification impact

None.

## Documentation impact

None. `docs/engineering/database.md` ("A setup waiting for another's migrations holds no transaction
and runs no statement… `test_setup_live.py` holds both") is still accurate.

## Last updated

2026-11-08
