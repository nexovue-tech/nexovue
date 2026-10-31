import type { CheckCounts, ProjectTotals } from "./api";

/**
 * What the screen is allowed to say when the feed is empty.
 *
 * An empty feed is not evidence of health, because no engine writes findings yet
 * (`api.ts`). So the headline never speaks for the whole system. It reports what a
 * probe actually said about the checks, and a check that has not run is a gap in what
 * we can see, not a failure.
 *
 * Errors are deliberately not a headline. A nonzero error rate is normal for most
 * services, and deciding when it is not is the engine's job. They stay on screen as a
 * reading, and F1 AC1.7's "all systems normal" waits for that engine
 * (`docs/changes/findings-false-all-clear/bug.md`).
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
  if (checks === undefined || checks.total === 0) {
    return { title: "Here is what this project reported", wrong: false };
  }
  if (checks.passing === checks.total) {
    const title =
      checks.total === 1
        ? "The synthetic check is passing"
        : `All ${checks.total} synthetic checks are passing`;
    return { title, wrong: false };
  }
  if (checks.passing === 0) {
    return { title: "No synthetic check has run in this window", wrong: false };
  }
  return {
    title: `${checks.passing} of ${checks.total} synthetic checks are passing, and the rest have not run`,
    wrong: false,
  };
}
