# Status: Live tests on a Mac

## Lifecycle status

Implemented, in review.

## Current task

None.

## Completed

- The time-zone helpers, the test clock and the memory test's platform condition are fixed.
- `just check` passes: 9,492 Python passed, 4 skipped; web 1,515; browser SDK 124. The memory test passes inside the Linux `scheduler-light` container.

## Next action

The founder decides whether to commit it on `feature/anomaly-detection`.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- `test_trace_detail_live.py:75` and `test_erasure_live.py:321` also write naive times. They pass here and were left alone.
- A project or organization removed while a sweep runs fails that sweep with a foreign key error (`fk_jobs_org_id_organizations`, seen when a test dropped its tenant). The next sweep runs normally, and nothing here changes that. It is a product behaviour to look at separately.
