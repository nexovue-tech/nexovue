# Bug: Overview's charts dip at their right edge, because the newest bucket is still filling

## Classification

Bug — a misleading reading on the console's own Overview charts. Found while building F20's
dashboards (plan T4.9, spec FR66); **not fixed there, by the plan**, and recorded here.

## Reported behavior

On a live window, the newest point of each rate chart on Overview reads lower than the steady
traffic around it, so a healthy service looks as if its traffic is collapsing "right now". At
24 hours the dip is deep, since one point there is a 24-minute bucket.

## Reproduction

Read from the code, and the arithmetic reproduced through the same statement shape, **not run on
the Overview screen itself**:

- Overview reads `/services/overview` for a live window whose end the console rounds **up** to the
  next 10 s (`apps/web/src/shared/selection.ts`, `resolveWindow`).
- `services/service.project_trend` buckets `OVERVIEW_TREND_SQL` with `toStartOfInterval`, and
  divides each bucket's count by the **whole** bucket (`rollup.per_minute(count, bucket_minutes)`).
- The newest bucket holds only the minutes up to now, and its newest minute only the requests
  counted so far, so its rate reads low by the share of the bucket not yet lived.
- F20's live test `test_dashboard_panels_live.py::test_ac114_a_live_windows_filling_bucket_is_marked_and_no_whole_bucket_dips`
  writes a steady fixture and reads it through `TREND_SQL` (the same `per_minute` rule): every
  whole bucket reads the steady rate and the newest reads lower, at 1 hour and at 24 hours.

## Root cause

A rate is divided by the time a bucket spans, not by the time it has covered. On a live window
the newest bucket has not ended.

## Specification impact

None to the console's specification: a chart should never show a fall that did not happen.
F20 handles it on dashboards only (FR66: the batch marks `incomplete_from`, and the panel leaves
that bucket off the line and reads it "so far").

## Regression test

To write with the fix: a steady fixture read through `/services/overview` on a live window, at 1
and 24 hours, whose newest point is either left off or marked, seen failing first.

## Fix plan and validation

Two candidates, for whoever takes this up; not decided here:

1. The same rule as F20: `/services/overview` (and `/services/trend`) mark the bucket still
   filling, and Overview's plots (`shared/overview-plots.ts`) leave it off the line.
2. Divide the newest bucket by the time it has covered. Simpler on screen, but a bucket whose
   minutes the workers have not flushed yet still reads low, so it moves the dip rather than
   removing it.

Recommendation: (1), since it is already built and tested for dashboards, and the two screens
would then agree.

## Status

Open. Recorded 2026-11-09 by F20 Step 4 (T4.9); not scheduled.
