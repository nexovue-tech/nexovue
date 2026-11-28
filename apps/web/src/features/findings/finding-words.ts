import { allowedNoun, lastsFor } from "@/shared/slo/words";

import type { CaseResolution, ClosedReason, LinkedBurn } from "./api";

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

// F19 (design, *Elsewhere*; AC85): a reliability target's burn linked to this finding.
// Each kind keeps its own noun, so a latency target never says "failures" and a check
// never says "requests" (AC89). AC13's scan reads this file.
const STILL_ALLOWED: Record<string, string> = {
  service_latency: "Slow requests still allowed",
  check_availability: "Failed runs still allowed",
};

/**
 * The burn beside a finding, with its rate and what is left while it is open (AC85).
 * An ended burn's figures are those of its last minute, so they are not shown.
 */
export function linkedBurnLine(burn: LinkedBurn, ended: boolean): string {
  const noun = allowedNoun(burn.kind ?? "");
  if (ended) return `Was using ${noun} too fast: ${burn.label}.`;
  const said = [`Also using ${noun} too fast: ${burn.label}.`];
  if (burn.rate !== null) said.push(`${Math.floor(burn.rate)} times faster than allowed.`);
  const lasts = lastsFor(burn.hours_left);
  if (lasts !== null) {
    said.push(lasts);
  } else if (burn.left !== null && burn.allowed !== null) {
    const words = STILL_ALLOWED[burn.kind ?? ""] ?? "Failures still allowed";
    said.push(`${words}: ${Math.max(burn.left, 0)} of ${burn.allowed}.`);
  }
  return said.join(" ");
}

/**
 * F4 T7.2: the note box's sentence, shown only while chat is on for the case's project, since
 * its notes then go to the AI provider the owner agreed to (spec, *What leaves this server*).
 */
export const chatSendsNotesSentence = (provider: string | null) =>
  `With chat on, notes can be sent to ${provider ?? "your AI provider"}.`;
/** Chat on and the project not left out: every standing but off, AI off and opted out. */
export const CHAT_ON_STANDINGS: readonly string[] = ["ready", "no_consent", "no_data"];

/** F4 FR41: a resolved case's note, copied into a runbook that outlives the finding. */
export const SAVE_AS_RUNBOOK = "Save as runbook";
export const SAVED_AS_RUNBOOK = "Saved as a runbook, with the case's fact line.";
export const OPEN_SAVED_RUNBOOK = "Open the runbook";
export const SAVE_ANYWAY = "Save anyway";
export const NOT_SAVED_AS_RUNBOOK = "The runbook was not saved. Try again.";
