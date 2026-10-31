import { UNKNOWN } from "@/shared/format/number";
import type { ZonedOptions } from "@/shared/format/zoned";
import { formatCalendarDay, formatZonedTime } from "@/shared/format/zoned";

import type { CheckCounts, DetectionStatus, ProjectTotals } from "./api";

/**
 * What the screen is allowed to say about the project's health. Two lines, from two
 * sources, and neither speaks for more than it saw.
 *
 * **Detection's line** (`detectionHeadlineFor`, C18) goes first. It may call services
 * normal, because the engine judged them, but only the judged ones: it says so while
 * others are still learning (AC42), and says detection is behind or stopped rather than
 * calling anything normal when it is (FR25, AC26).
 *
 * **The checks' line** (`headlineFor`) reports what a probe said about the checks, and a
 * check that has not run is a gap in what we can see, not a failure. It never claims
 * the whole system is healthy (`docs/changes/findings-false-all-clear/bug.md`), and no
 * line on this screen ever says "All systems normal" on its own.
 *
 * Errors are deliberately not a headline. A nonzero error rate is normal for most
 * services, and deciding when it is not is the engine's job.
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

/**
 * Detection's line, or nothing when it has nothing true to add.
 *
 * Nothing when the status was not read: the screen shows its error state for a failed
 * read, and an absent status must never fall through to a healthy line. Nothing, too,
 * while a finding is open, because then the feed is the answer. Times are on the clock
 * `at.zone` names, the browser's until the project's own is read.
 */
export function detectionHeadlineFor(
  status: DetectionStatus | undefined,
  at: ZonedOptions = {},
): Headline | undefined {
  if (status === undefined) return undefined;

  switch (status.state) {
    case "no_services":
      return undefined;
    case "not_running":
      return {
        // "Has not run since 14:02" when the last run is known; plain "is not running" when it is not.
        title: since(status.not_running_since, at)
          ? `Detection has not run${since(status.not_running_since, at)}`
          : "Detection is not running",
        wrong: true,
      };
    case "data_delayed":
      // Our lateness, not their outage: said plainly, and not drawn as wrong.
      return {
        title: `nexovue is behind on reading your data${since(status.delayed_since, at)} — your services may be fine`,
        wrong: false,
      };
    case "learning": {
      const starts = known(status.earliest_judging_date, formatCalendarDay);
      return {
        title: `Learning your services' normal${starts ? `. Judging starts on ${starts}` : ""}`,
        wrong: false,
      };
    }
    case "judging": {
      if (status.open_findings > 0) return undefined;
      const learning = status.services_learning;
      const rest =
        learning > 0 ? `. ${learning} ${learning === 1 ? "is" : "are"} still learning` : "";
      return { title: `All judged services are normal${rest}`, wrong: false };
    }
  }
}

/** " since 14:02 London time", or nothing when the time is missing or unreadable. */
function since(iso: string | null, at: ZonedOptions): string {
  const when = known(iso, (value) => formatZonedTime(value, at));
  return when ? ` since ${when}` : "";
}

function known(value: string | null, format: (value: string) => string): string | undefined {
  if (value === null) return undefined;
  const formatted = format(value);
  return formatted === UNKNOWN ? undefined : formatted;
}
