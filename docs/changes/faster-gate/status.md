# Status: faster-gate

## Lifecycle status

**Released to `main`** as `2f3d46f`, "The gate seeds the window it asserts on, and stops going red with time", after the founder read the diff on
2026-09-21. Not deployed — nexovue has no deployment target.

## Current task

None.

## Completed

All three levers, each measured. See [plan.md](plan.md) for the tasks.

| | Before | After | How |
|---|---|---|---|
| `test_purge_live.py` | 57s | **25s** | teardown defers to `clickhouse_cleanup` instead of waiting on 12 purges |
| `test_deletion_live.py` | 34s | **23s** | the same, for its telemetry fixture |
| `test-py` (whole suite) | 248s | **61s** | `pytest -n 6 --dist loadfile` |
| `just check` | 284s | **95s** | the above |
| `just e2e` | 186s | **87s** | an address per worker, so four callers are counted as four; six workers |
| **check + e2e** | **470s** | **182s** | |

## Next action

None — this is in history.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **Parallelism can hide order dependence.** Nothing new appeared across five full runs at four, six
  and eight workers, and the only failure in any of them is the pre-existing environmental one below.
  What would catch a regression is the gate itself failing intermittently; if that starts, `-n 0`
  restores the serial run for comparison in one edit.
- **`TRUSTED_PROXY_COUNT: 1` in development means the API believes `X-Forwarded-For`.** True of this
  topology — the web container is the one proxy — and the API is also published directly on 18000,
  where anything on the machine could claim any address. That is a development convenience and is
  said so in the compose comment. The deployment files set the count from their own topology and are
  untouched.
- **A deferred ClickHouse cleanup leaves rows until the session ends.** They belong to a UUID tenant
  nothing else reads, which is the argument `clickhouse_cleanup.py` already makes for every other
  suite. Under `-n 6` there are six sessions, so six cleanups — still one mutation per table each,
  issued without waiting.

## Validation

- `just check`: **4114 passed, 3 skipped, 95s**. Format, lint, `mypy`, Go, web, SDK and the contract
  checks all ran and passed.
- `just e2e`: **22 passed, 87s**.
- **Stability**: the Python suite was run five times (n=4, 6, 6, 6, 8) and the e2e suite four times
  (4, 6, 8, 6 workers). Same counts every time.
- **The one failure**, in every run including the serial baseline, is the environmental one recorded
  in `docs/changes/console-review-fixes/status.md`: `test_alert_telemetry_live.py::test_error_groups_
  come_back_with_the_errors_screens_identity` needs a span carrying an exception *event* in the last
  24 hours, which only `just otel-demo up` produces here. It fails identically on a clean worktree of
  `HEAD`.
- **What was not done:** nothing was skipped, marked flaky, deleted or given a longer timeout. The
  same tests run against the same real stores.

## Specification impact

None.

## Documentation impact

The measurements live in this record and in the two comments that carry the decisions —
`justfile`'s `test-py` recipe and `playwright.config.ts`'s `workers`. `docs/engineering/` says
nothing about the gate's duration that has changed.

## Last updated

2026-09-21
