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
    case "too_many_services":
      return {
        label: "Too many services",
        detail:
          "This project has more services than detection judges. Only the busiest are judged, and this is not one of them.",
        tone: "attention",
      };
    case "no_incoming_requests":
      return {
        label: "Not judged: no incoming requests",
        detail:
          "None arrived in the last window, and its normal expects too few for that to be a problem.",
        tone: "quiet",
      };
    case "no_detection_yet":
      // A service with only outgoing or internal calls stays here: it has no incoming requests.
      return {
        label: "No detection yet",
        detail:
          "Detection judges a service's incoming requests, and has judged none of this one's yet.",
        tone: "quiet",
      };
    case "learning":
      return learningBadge(detection.signals);
    case "judged":
      return { label: "Judged", detail: notJudgedDetail(detection.signals), tone: "quiet" };
  }
}

/** What a signal judged later than the rest is, in a sentence's opening words. */
const JUDGED_LATER: Record<SignalKind, string> = {
  slower: "Slowness is",
  failing: "Failures are",
  traffic: "Traffic drops are",
};

/**
 * "Learning until 3 Nov": the day judging starts, as the feed's headline says it. A
 * signal that waits longer, as traffic drops wait 3 weeks (spec, *Error cases*), is
 * named beside it, so neither day contradicts the other.
 */
function learningBadge(signals: readonly SignalStatus[]): Badge {
  const learning = signals.flatMap((each) =>
    each.learning_until === null ? [] : [{ signal: each.signal, day: each.learning_until }],
  );
  const first = learning.map((each) => each.day).sort()[0];
  if (first === undefined) return { label: "Learning", detail: null, tone: "quiet" };
  const later = learning
    .filter((each) => each.day > first)
    .map((each) => `${JUDGED_LATER[each.signal]} judged from ${formatCalendarDay(each.day)}.`);
  return {
    label: `Learning until ${formatCalendarDay(first)}`,
    detail: later.length > 0 ? later.join(" ") : null,
    tone: "quiet",
  };
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
