import type { CheckCounts, ProjectTotals } from "./api";

/**
 * What the screen is allowed to say when the feed is empty.
 *
 * An empty feed is not evidence of health. No engine writes findings yet
 * (`api.ts`), so "All systems normal" was printed on every project — including
 * one whose own evidence line, two rows below it, read "0 of 4 synthetic checks
 * passing" (`docs/quality/console-review-2026-09-20.md`, D1).
 *
 * So the headline is derived from the readings the screen already holds, and it
 * claims nothing beyond them. It says a check is down only when a probe
 * actually reported it down: a check that has not run in this window is a gap in
 * what we can see, not a failure, and the two must not be written the same way.
 *
 * Errors are deliberately not a headline. A nonzero error rate is normal for
 * most services and is already on screen as a reading; promoting it here would
 * make the screen cry wolf, which costs more than the silence it replaces.
 */

export interface Headline {
  title: string;
  /** True when the title names something wrong, and the screen should look it. */
  wrong: boolean;
}

export function headlineFor(totals?: ProjectTotals, checks?: CheckCounts): Headline {
  if (totals === undefined) {
    return { title: "Nothing has reported to this project yet", wrong: false };
  }
  if (checks !== undefined && checks.down > 0) {
    const what = checks.down === 1 ? "A check is" : `${checks.down} checks are`;
    return { title: `${what} down`, wrong: true };
  }
  return { title: "All systems normal", wrong: false };
}
