# Status: Detection false delayed

## Lifecycle status

Implemented, in review.

## Current task

None.

## Completed

- The API and the scheduler share one limit: the pipeline is behind at a gap of 3 minutes, not 2 or 1.
- Regression tests seen failing, then passing. On the dev stack, no `the pipeline is behind` warning in 3 sweeps after the fix.

- The real Findings page, signed in on the dev stack, reads "All judged services are normal. 2 are still learning" after the fix.
- `just check` passes with `live-tests-on-a-mac`, which fixed the 5 live tests that failed on the first run: 9,492 Python passed, 4 skipped; web 1,515; browser SDK 124.

## Next action

The founder decides whether to commit it on `feature/anomaly-detection`.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- The limit depends on the worker writing progress about once a minute. A much longer `WORKERS_METRICS_LATE_GRACE` would widen the healthy gap.
- A hold under 3 minutes shows no *data delayed*. Its minutes are still not judged until they settle.
