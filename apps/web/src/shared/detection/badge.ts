import { formatCalendarDay, formatZonedTime } from "../format/zoned";
import type { ServiceDetection, SignalKind, SignalStatus } from "./api";

/**
 * The words on a service's detection badge (FR25, design *The service screen*).
 *
 * Every state says what is true, and none of the ones that are not judging may read
 * as healthy (BR4): *Judged* is said only when the API says the service is judged. A
 * read that failed is its own state, never the absence of a badge.
 */

export type BadgeTone = "quiet" | "attention" | "critical";

export interface Badge {
  label: string;
  /** The longer sentence beside it, when one helps. */
  detail: string | null;
  tone: BadgeTone;
}

const SIGNAL_WORDS: Record<SignalKind, string> = {
  slower: "slowness",
  failing: "failures",
  traffic: "traffic",
};

export function describeBadge(
  detection: ServiceDetection | undefined,
  { failed, zone }: { failed: boolean; zone?: string },
): Badge | null {
  if (failed) {
    return {
      label: "Detection unavailable",
      detail: "Its state could not be read, so it is not shown as judged.",
      tone: "critical",
    };
  }
  if (detection === undefined) return null;
  switch (detection.state) {
    case "not_running":
      return {
        label: "Detection not running",
        detail: "Nothing about this service is being judged right now.",
        tone: "critical",
      };
    case "data_delayed":
      return {
        label: "Data delayed",
        detail: "Judging waits until this service's minutes have all arrived.",
        tone: "attention",
      };
    case "muted":
      return {
        label: "Muted",
        detail: detection.muted_until
          ? `Findings are recorded, and nobody is told, until ${formatZonedTime(detection.muted_until, { zone })}.`
          : null,
        tone: "attention",
      };
    case "no_detection_yet":
      return {
        label: "No detection yet",
        detail: "Detection has not judged a minute of this service yet.",
        tone: "quiet",
      };
    case "learning":
      return { label: learningLabel(detection.signals), detail: null, tone: "quiet" };
    case "judged":
      return { label: "Judged", detail: notJudgedDetail(detection.signals), tone: "quiet" };
  }
}

/** "Learning until 3 Nov": the latest day any signal is still learning. */
function learningLabel(signals: readonly SignalStatus[]): string {
  const days = signals
    .map((each) => each.learning_until)
    .filter((day): day is string => day !== null)
    .sort();
  const last = days.at(-1);
  return last === undefined ? "Learning" : `Learning until ${formatCalendarDay(last)}`;
}

/** What a judged service still cannot be judged on, signal by signal (BR4). */
function notJudgedDetail(signals: readonly SignalStatus[]): string | null {
  const parts = signals.flatMap((each) => {
    if (each.state === "too_little_traffic") {
      return [`too little traffic to judge ${SIGNAL_WORDS[each.signal]}`];
    }
    if (each.state === "learning" && each.learning_until) {
      return [
        `${SIGNAL_WORDS[each.signal]} learning until ${formatCalendarDay(each.learning_until)}`,
      ];
    }
    return [];
  });
  if (parts.length === 0) return null;
  const sentence = parts.join("; ");
  return `${sentence[0]?.toUpperCase() ?? ""}${sentence.slice(1)}.`;
}
