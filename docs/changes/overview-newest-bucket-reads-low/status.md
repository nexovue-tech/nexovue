# Status: overview-newest-bucket-reads-low

## Lifecycle status

**Recorded, not started.** Found by F20 Step 4 (plan T4.9), which fixes the same fault on
dashboards only.

## Current task

None.

## Completed

- The record (`bug.md`), 2026-11-09.

## Next action

Founder: decide when to schedule it, and choose between the two fixes in `bug.md`
(recommendation: the dashboards' rule, so both screens agree).

## Open decisions

Which fix: mark the filling bucket and leave it off the line, or divide it by the time covered.

## Blocked

Nothing.

## Risks

Until fixed, Overview's live charts can read as traffic falling at their right edge.
