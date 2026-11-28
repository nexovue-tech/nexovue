# Bug: Phase 2's checks fail on the founder's Mac

## Reported behavior

On 2026-11-27, `just check` on the founder's Mac (macOS, Asia/Kolkata) could not run the Python tests,
and once they ran, 44 Python tests and one web test failed. CI, on Linux in UTC, was green on the
same commit (`a4af561`).

## Reproduction

- `just check` stopped at `test-py`: `timeout: command not found` (exit 127). macOS ships no
  coreutils `timeout`.
- With the Python tests run directly, 215 failed. 172 came from the local `.env`'s
  `PAYMENTS_DOMESTIC_*` settings, which a self-hosted install now refuses on purpose; that is local
  configuration, commented out in the founder's `.env`, not a code change.
- The other 43 passed with `TZ=UTC` and failed with the Mac's own zone. Measured with
  clickhouse-connect 1.11.0: a naive `datetime` written through `insert` is read as the machine's
  local time (12:00 is stored as 06:30 in Asia/Kolkata); a naive query parameter is not converted.
- `test_insight_role.py`'s memory reading came back `None`: it reads Linux's `/proc/<pid>/smaps_rollup`.
- `trend-chart-options.test.ts` stored `tzDate: 19800000`, the shifted timestamp uPlot's `tzDate`
  makes, which depends on the machine's own zone (0 on the Mac, 19,800,000 in UTC).

## Root cause

The tests and one tool assumed the machine runs Linux in UTC, as CI and the images do: a Linux-only
command and file, naive times handed to a client that converts them with the local zone, and a
snapshot of a machine-dependent number. Product code is not affected: the scheduler's only ClickHouse
insert refuses naive times (`detection/store.py`, `write`).

## Specification impact

None.

## Regression test

Each fix was seen failing first: the five live suites failed 43 tests under `TZ=Asia/Kolkata` and
pass under both zones; the snapshot test failed under Asia/Kolkata and passes under UTC,
Asia/Kolkata and America/New_York; the watchdog was tested with a finishing and a hanging command.

## Fix plan and validation

- `justfile`: a plain-bash watchdog in place of `timeout`, pytest in the foreground (a background job
  ignores INT), INT at 40 minutes, KILL a minute later, no stray `sleep` left behind.
- Aware UTC times for every value written through `insert` in `test_traces_live.py`,
  `test_evidence_link_traces_live.py`, `test_assistant_tools_live.py`,
  `test_evidence_store_live.py` and `tools/ai-eval/fix/seed_findings.py`.
- `test_insight_role.py` skips only its memory reading where `smaps_rollup` does not exist, as
  `test_jobs_live.py` already does.
- The snapshot records the wall clock the chart shows for 00:00 UTC (`05:30` in Asia/Kolkata).

Validated on the Mac: format, lint and mypy (1,357 files) pass; Go passes; Python 17,221 passed,
8 skipped, 0 failed; web 2,716 and browser SDK 124 passed; contracts and bundle checks pass.

## Status

Fixed; see `status.md`.
