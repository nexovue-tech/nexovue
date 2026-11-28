# Change: CI runs the Python tests in two halves

## Classification

Infrastructure

## Current behavior and problem

CI's `check` job ran `just check` whole on one runner: 28 to 37 minutes against its 45-minute step
limit (37 min 19 s on run 124, 2026-11-23). As tests are added it would fail on time alone, and the
release workflow publishes only a commit whose CI passed, so a slow run would also block a release.

## Intended behavior

`check` runs as three parts on three runners, each with its own stack: everything but the Python
tests (`fmt-check lint test-go test-web contracts-check bundle-check`), and the Python tests in two
halves. A final job keeps the name `check`, the required status, and passes only when every part
passed, so the branch protection setting does not change. `just check` on a laptop is unchanged.

The halves come from `tools/test-shard/shard.py`, which asks pytest which files it collects (so
markers and `collect_ignore` hold) and splits files, not tests, by test count. `just test-py` takes
an optional list of files.

## Affected specifications and architecture

None. `docs/engineering` describes `just check`, which is unchanged.

## Risks and rollback

- A test that only passes after another file's test would now fail in one half; none did locally.
- Three stacks cost three runners' minutes instead of one.
- Rollback: revert the commit; `check` becomes one job again.

## Plan and validation

- Two halves collect 8,618 and 8,617 tests, adding up to the whole run's 17,235 (checked by collecting
  each). The fixture repository's tests, kept out by `collect_ignore`, stay out.
- Half 2 run the way CI runs it, on the founder's Mac: 8,612 passed, 5 skipped, 4 minutes.
- `tools/test-shard/test_shard.py`: 5 tests. `actionlint` passes on the workflow.

## Status

Implemented; CI on pull request #25 confirms.
