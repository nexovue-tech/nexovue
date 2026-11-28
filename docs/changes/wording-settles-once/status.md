# Status: Wording settles once

## Lifecycle status

Implemented, awaiting review (worktree `wt/f3fix`, on `feature/phase-2` 301f450). Not committed.

## Current task

None.

## Completed

- Both reported defects checked in the code and reproduced (see [`bug.md`](bug.md), *Reproduction*).
  Both confirmed as reported. One addition found while reading: the two sweep tests swept with a
  clock ahead of real time, so their sweep could take another test's run in flight. Fixed with
  defect 2, not reproduced.
- **Fix 1:** `insight/store.py` `finish_wording` ends the run first and moves money only if it did,
  in one transaction; it returns `written`, `not_written` or `already_ended`. `insight/word.py` logs
  `already_ended` as a warning and returns `stale`. Design *Limits* states the rule;
  `docs/operations/ai.md` tells the operator what the warning means.
- **F4 chat checked:** not affected (its SQL functions clamp to what each question holds). Its sweep
  test now pins that a late spend and a late final write move nothing.
- **Fix 2:** a `now` fixture per test in `wording_fixtures.py`; `connect`, `finding`, `version` take it
  as a required keyword, `run_word` defaults to it through `Connected.now`. 25 test functions across
  `test_insight_word_live.py` (20), `test_insight_limits_live.py` (3) and the canary's fixture (2
  tests) moved to it; no assertion was weakened. Sweep tests start their run 3 minutes back and
  sweep 1 second past it.
- `/review-diff`: every changed file is explained by `bug.md`. Tenancy: no statement added; every
  statement still binds `org_id`. No route, migration, ClickHouse statement, masking or web change.
  No secret in any log or fixture. One MEDIUM found and fixed: the new warning was not in the
  operator runbook. No finding left open.

## Next action

The coordinator reviews the diff on `wt/f3fix` and decides whether to commit it to
`feature/phase-2`, and the founder decides the open decision below.

## Open decisions

1. **What a late wording finish does.** Decided here on recommendation: nothing (the sweep's charge
   stands; late tokens beyond the reservation are not counted; the words are dropped), the same as
   F4's chat already does. The alternative is counting the excess over the reservation. Nothing is
   blocked; reversible at any time.
2. **The testing standard's new rule** (`docs/engineering/testing.md`: a test's clock is read when
   the test starts; a test's own cross-organization sweep reaches only its own rows). Proposed in
   this change because the defect exposed the gap (the alert suites' `future()` and the 2026-11-09
   gather CI fix are earlier cases of the same kind). The founder accepts or edits it.

## Blocked

Nothing.

## Risks

- **Not ours, found while validating:** `test_insight_account_live.py` deliberately opens 21
  connections as `nexovue_insight` (its limit is 20). Any other insight suite running at that moment
  in another xdist worker fails with `TooManyConnectionsError` (seen once here:
  `test_a_key_that_will_not_open_for_this_address_fails_without_a_call`). The CI gate runs the Python
  suite in parallel with `--dist loadfile`, so this can make CI red at random. Not fixed here; it
  needs its own record (for example, running that test against its own role or under an xdist group).
- The deadlock between the old finish and the sweep (opposite lock orders) was read from the code,
  not observed. The fix makes both take the run row first, either way.
- The stale-clock reproduction is probabilistic in its real form: in the harness only the test
  that holds a run 0.5 s at its provider failed every time (2 of 2); the tests that hold a killed
  run for milliseconds did not.
- A late finish whose actual passed its reservation is under-counted by that excess (decision 1).

## Validation

Own containers `f3fix-postgres` (25432) and `f3fix-clickhouse` (28123 HTTP, 29000 native), both
stores migrated to head (Postgres 0075, ClickHouse 42 migrations), ports checked before each run.

**Seen failing, then passing** (each break restored byte-equal, checked with `sha256sum -c`):

| Break | Test | Failed with | Restored |
|---|---|---|---|
| `store.py` and `word.py` back to HEAD | `test_a_run_finishing_after_the_sweep_ended_it_changes_nothing` | `assert (2002, 0) == (1542, 1542)`: counted twice, the other run's reservation erased | 1 passed |
| `word.py` alone back to HEAD | same | `assert ('done' == 'stale'` | 1 passed |
| `now` fixture 20 minutes stale, with the real-clock sweep harness | the three wording suites | `test_four_parallel_jobs_of_one_version_make_one_call_and_one_result`: `['stale', …] == ['done', …]`; harness took 1 run as `died`; 1 failed, 43 passed | 44 passed, harness took 0 rows |
| (reproduction) HEAD's suites, `NOW` 20 minutes back, with the harness | same | the same test, same assertion; 1 failed, 42 passed | — |
| `assistant_record_spend` in my own container without its in-flight-or-reserved guard (definition saved, restored, hash equal) | `test_the_sweep_settles_a_dead_question_and_charges_what_it_held` | `assert ('unavailable', 0, 60500) == ('unavailable', 0, 60000)` | 9 passed |

The harness (`stack_sweep.py`) is a pytest plugin in the scratchpad, not in the repository.

**Checks, final state:**

- `pytest -n 2 --dist loadfile` on `test_insight_*` (except `test_insight_account_live.py`),
  `test_assistant_budget_live.py`, `test_assistant_account_live.py`,
  `apps/api/tests/test_scheduler_postgres_tenancy_structure.py`: **92 passed**.
  `test_insight_account_live.py` run alone (see *Risks*): **39 passed**.
- `uv run mypy .`: no issues in 1082 source files. `uv run ruff check .`: all checks passed.
  `uv run ruff format --check .`: 1466 files already formatted.
- `just check` and the full live gate were not run, by instruction.

## Specification impact

None to the specification. Design *Limits*: one sentence states the settle-once rule.

## Documentation impact

`docs/features/ai-insights/design.md` (*Limits*), `docs/operations/ai.md` (the late-finish
warning), `docs/engineering/testing.md` (proposed rule, open decision 2), F3's `status.md` (pointer).

## Last updated

2026-11-21
