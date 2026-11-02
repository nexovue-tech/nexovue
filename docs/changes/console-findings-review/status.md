# Status: Console findings review

## Lifecycle status

Implemented, in review.

## Current task

None.

## Completed

- Fixes 1, 2, 3, 5, 6 and 7 in `bug.md`. Fix 4 is recorded as no change.
- On a production build: 8 reads, no 429, no `use()` error; six service screens opened in 15 seconds with no 429; a snooze survives a reload, shows in the feed and can be ended; units and the new title read correctly.

- The chart's axis reads in UTC beside a title in UTC, checked on the page.
- `just check` passes: 9,498 Python passed, 4 skipped; web 1,519; browser SDK 124.

## Next action

The founder decides whether to commit it on `feature/anomaly-detection`.

## Open decisions

None.

## Blocked

Nothing.

## Risks

- The quota of 180 lets one person send three times the reads. The cap of 8 still bounds what scans at once, and the organization's limits are unchanged. It is a first figure, with no production measurement.
- A waiting read holds its connection for up to 5 seconds, and an aborted read still holds its slot until it finishes.
- The seed's live-rate history has only a unit test. The dev project already has history, and the seed will not overlap it.
- The `use()` warning stays in development until TanStack Router changes.
- Other screens' charts still read in the browser's zone.
