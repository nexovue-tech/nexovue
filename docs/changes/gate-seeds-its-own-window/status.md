# Status: gate-seeds-its-own-window

## Lifecycle status

**Implemented, awaiting the founder's review of the diff.** Nothing is committed.

## Current task

None.

## Completed

- **`tools/gateseed`** — sends traces, one exception event and a cumulative histogram over
  OTLP/JSON, then waits for the ingest path to make them readable. No external dependencies.
- **The wait is counted against a baseline**, not a clock. The first version bounded the count by
  `received_at >= now - 1m`, and a second run inside that minute was satisfied instantly by the
  first run's rows: measured **12.3s, then 7ms, then 8ms** — the wait it exists for, skipped.
  `Baselines` counts each statement before anything is sent and the wait is for that many more.
  Held by `TestAnArrivalIsCountedAgainstItsOwnBaseline`, which fails any statement that bounds
  itself by time.
- **The seeded traces are slow on purpose**, so tail sampling keeps all of them.
- **`test_metrics_live` discovers a histogram** rather than naming the demo's.
- **`justfile`** — `gate-seed`, `test-py: gate-seed`, and the module in `go_modules`.

## Next action

Founder: review the diff and say **push**.

## Open decisions

None. This closes the one recorded in `docs/changes/console-review-fixes/status.md` — three ways
out were listed (seed the window, widen the lookback, let them skip) and the founder chose seeding
on 2026-09-21.

## Blocked

Nothing.

## Risks

- **`dbstats` is not seeded and is not covered.** It reads database statistics the demo's collector
  publishes, which this tool does not produce. It passes today because that data has a longer life
  than the two-day window, but it will go red eventually and this change does not stop that.
  Recorded rather than papered over.
- **The 12.4s is the ingest path's latency, not the tool's**, so it will move with the worker's
  assembly window. If the gate's budget ever matters more than this, the seeder could write to
  ClickHouse directly — at the cost of no longer proving the ingest path works, which is part of
  what makes these suites worth having.
- **Two arrivals count rows by service name and metric name.** Anything else writing under
  `gateseed` or `gateseed.request.duration` would satisfy the wait. Nothing does, and the baseline
  makes a stale row harmless, but it is an assumption rather than a proof.

## Validation

- **`just check`, exit 0, in one run** — the first fully green gate this session:
  **4119 Python passed, 2 skipped, 0 failed**; **1335 web** (123 files); **120** browser SDK; Go,
  format, lint, `mypy` (572 files) and the contract checks clean. **1m53s** total.
- **Before this change, on the same commit**: 4 failed — three in `test_metrics_live`, one in
  `test_alert_telemetry_live` — plus two in `test_tenant_isolation_live` when run directly.
- **Seed cost measured over three runs**: 12.37s, 12.37s, 12.35s.
- **Seen red before green**, each with the message that would have saved the time:
  - `TestTracesAreSlowEnoughToSurviveTailSampling` — set `SlowEnough` to 12ms: *"a seeded trace took
    12ms, at or under the 2s the sampler keeps above."*
  - `TestMetricsCountsRiseWithinASeries` — flatten the counts: *"series 0 export 0 reports 0, not
    above the previous 0."*
- **The sampling discovery was measured, not reasoned**: 80 traces sent, 11 rows stored, which is
  the shipped policy working correctly.
- **The bucketless-histogram discovery likewise**: the first run of the new fixture picked
  `ad`/`kafka`'s histogram, which has no `explicit_bounds`, and the merge test failed on correct
  behaviour.
- `golangci-lint` found five real things in the first draft — two unchecked `Body.Close`, two
  repeated string keys, one unprovable `int → uint64`. All fixed at the source rather than
  suppressed; no `//nolint` added.

## Specification impact

None.

## Documentation impact

`docs/changes/console-review-fixes/status.md` — the open decision this closes.

## Last updated

2026-09-21
