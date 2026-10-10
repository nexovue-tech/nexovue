# Bug: the synthetics live suite fails for two days after any outage of a check's target

## Reported behavior

`apps/api/tests/test_synthetics_live.py` errored in eight tests during the `just check` of
`ingest-cannot-be-knocked-over` (2026-10-10), all at fixture setup:

```
AssertionError: no successful synthetic probe in ClickHouse for the last two days. This suite
asserts on real prober output, so an empty store is a failure here, not a pass — check that the
scheduler is running and that at least one check is passing.
```

## Reproduction

The development stack's two checks both probe the OpenTelemetry demo's storefront
(`http://host.docker.internal:8080/`). The demo stack no longer exists on the machine. Per check, in
`metrics_gauge` over the last two days: `up` 287 and 285 runs, the last at 2026-10-09 18:34; `down`
414 runs each since. `just check` then: `4166 passed, 3 skipped, 8 errors` — all eight this suite.

## Root cause

The `subject` fixture accepted only a check with **no failed run in the whole two-day lookback**
(`HAVING … countIf(… outcome NOT IN ('up', 'degraded')) = 0` over `timestamp >= since`), and every
test then read that whole lookback. The restriction is real for two assertions — a run that never
connected publishes fewer than five phase readings, which breaks "five phases per probe" and "every
bucket names every phase" — but applied to two days, one outage of the target disqualifies every
check until it has aged out of the window: up to 48 hours after the target is back.

Not a product defect, and not caused by the change whose check it failed in.

## Specification impact

None.

## Regression test

The suite itself: the fixture now picks the most recent **hour** in which a check ran and never
failed, and every assertion on real prober output reads that hour. Against the same store — two days
of runs, the last 17 hours all failed — the old fixture errored eight tests; the new one passes all
13. With no clean hour anywhere in the lookback it still fails, with the reason.

## Fix plan and validation

`apps/api/tests/test_synthetics_live.py` only: the fixture's query groups by check and hour and orders
by the newest clean hour; `SyntheticSubject` carries that hour; the stranger tenant is found by its
own query. The redelivery tests, which write their own rows at the current time, keep their own
window.

## Status

See [`status.md`](status.md).
