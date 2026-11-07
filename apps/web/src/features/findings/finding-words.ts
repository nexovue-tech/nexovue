import type { CaseResolution, ClosedReason } from "./api";

/**
 * How a closed finding closed, in the reader's words. Shared by the feed's card and
 * the finding's own page, so the two never describe one close differently.
 *
 * Never "Resolved": a finding that closed because the service went quiet, or because
 * nobody answered for a week, was not fixed, and saying so would be a claim we cannot
 * support.
 */
export const CLOSED_AS: Record<ClosedReason, string> = {
  recovered: "Recovered",
  false_alarm: "Closed as a false alarm",
  new_normal: "Accepted as the new normal",
  adopted: "Accepted as the new normal after 7 days with no answer",
  service_gone: "Service stopped sending",
  could_not_be_judged: "Closed: too quiet to judge for 6 hours",
};

/**
 * How a case ended, one phrase per resolution (F22, spec *Case states*). The
 * person's own resolve and a tracker's close carry their time; the self-endings
 * carry their fixed words, because the number is the sentence.
 */
export function resolutionWords(
  resolution: CaseResolution,
  { by, at }: { by?: string | null; at?: string | null } = {},
): string {
  const when = at ? ` at ${at}` : "";
  switch (resolution) {
    case "resolved":
      return `${by ?? "Someone"} resolved the case${when}`;
    case "tracker_closed":
      return `Closed in GitHub${when}, reopen here if that was wrong`;
    case "no_activity":
      return "Ended by itself after 90 days with no activity";
    case "recovered_untouched":
      return "Ended by itself 3 days after recovery. Nobody had added anything";
    case "false_alarm":
      return "Ended as a false alarm";
    case "new_normal":
      return "Ended as the new normal";
  }
}

/** AC63: no screen may claim nobody is working on a finding. */
export const NOBODY_HAS_TAKEN_THIS = "Nobody has taken this in nexovue";

/** FR14's note box words. */
export const NOTE_BOX_LABEL = "Add a note";
export const NOTE_BOX_WARNING = "Visible to everyone in your organization. Do not paste secrets.";
export const NOTE_DELETE_CONFIRMATION =
  "The text is removed. The timeline keeps that a note was deleted.";

/** FR18's fixed lines for a timeline the spell kept quiet. */
export const EMPTY_TIMELINE =
  "No events for this finding. It opened while this service was flapping, so openings and closings are not listed one by one.";
export const FLAPPING_JOINER = "This service is flapping";
