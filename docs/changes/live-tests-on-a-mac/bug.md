# Bug: five live tests fail on a Mac

## Reported behavior

`just check` on the founder's Mac, 2026-11-01: 5 failed, 9,488 passed, 2 skipped, stopping at `test-py`.

## Reproduction

Each test run on its own, on `feature/anomaly-detection`. The first four fail the same way with the banner fix stashed.

| Test | Decisive output |
|---|---|
| `test_the_new_normal_moves_the_normal_by_the_readings_and_closes_the_finding` | `ValueError: not enough values to unpack (expected 1, got 0)` |
| `test_the_new_normal_is_refused_until_enough_readings_say_what_it_is` | The response had no readings |
| `test_the_trend_is_the_leading_signal_s_last_readings_of_this_project_only` | `assert [] == [100.0, 200.0, 350.0]` |
| `test_a_child_that_grew_past_its_limit_is_replaced_after_its_job` | `assert '33871' != '33871'` |
| `test_a_sustained_slowdown_opens_exactly_one_finding_and_closes_it` | `assert 0 == 1`, in the full run only |

Measured:

- The first three pass with `TZ=UTC` and fail in the Mac's IST.
- The fifth's project was judged by the dev stack's `scheduler-light` during the test: `detection project judged … project_id=1b499414-…` at 17:38:36, with minutes on the real clock.

## Root cause

- **Three tests, time zone.** Their helpers in `test_findings_actions_live.py` and `test_findings_api_live.py` wrote UTC times with the zone removed. The ClickHouse client reads such a time in the machine's zone, so on IST the readings landed 5.5 hours early and outside the window read. CI runs in UTC, so it never showed.
- **One test, platform.** `private_bytes` in `apps/scheduler/src/nexovue_scheduler/jobs/worker.py` reads `/proc/<pid>/smaps_rollup`, which only Linux has. On a Mac no reading exists, so no child is replaced. The images are Linux, so the product is unaffected.
- **One test, shared stack.** `detection_history.ONSET` was noon today. An open finding makes a project active, so after noon UTC the real sweep could judge it at the real minute, past the test's clock. The test's later minutes were then never judged.

## Specification impact

None. Only tests change. No customer would have been affected by these failures.

## Regression test

- `test_the_running_scheduler_does_not_disturb_a_run_on_the_test_clock`, seen failing before the fix with `assert 0 == 1`.
- The three time-zone tests fail in IST before the fix, and pass in IST after it.
- The memory test now skips where `smaps_rollup` is absent, with the reason stated. It still runs on Linux.
