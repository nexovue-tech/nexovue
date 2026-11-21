/**
 * The reliability target words more than one feature says (F19 spec FR10, FR12): the
 * state names and the badge with its tone, each kind's noun for what it allows, and how long
 * what is left lasts. Here
 * because features may not import each other, and a sentence said in two places must not
 * drift: the target's page, a finding's linked burn and a dashboard's reliability-target
 * panel all say these. The AC13 word scan reads this folder for words the spec forbids.
 */

/** FR10's states, as the platform's State on the API names them. */
export type SloState =
  | "disabled"
  | "not_up_to_date"
  | "source_gone"
  | "running_out"
  | "counting"
  | "not_measured"
  | "no_recent_requests"
  | "building_history"
  | "nothing_left"
  | "too_little_traffic"
  | "on_track";

export const STATE_NAMES: Record<SloState, string> = {
  disabled: "Disabled",
  not_up_to_date: "Not up to date",
  source_gone: "Source gone",
  running_out: "Running out",
  counting: "Counting",
  not_measured: "Not measured",
  no_recent_requests: "No recent requests",
  building_history: "Building history",
  nothing_left: "Nothing left",
  too_little_traffic: "Too little traffic",
  on_track: "On track",
};

/** What a badge and its tone are worked out from: a target's state as the API sends it. */
export interface SloStanding {
  state: SloState;
  severity: "critical" | "warning" | null;
  /** Counted in the list's headline (FR10a): a silence past its usual length. */
  attention: boolean;
}

/** The design's tone table: words always, colour only beside them (rule 6). */
export type Tone = "critical" | "warning" | "neutral" | "none";

const WARNED: readonly SloState[] = [
  "nothing_left",
  "source_gone",
  "not_up_to_date",
  "not_measured",
];

export function toneOf(standing: SloStanding): Tone {
  if (standing.state === "running_out") {
    return standing.severity === "critical" ? "critical" : "warning";
  }
  if (standing.state === "on_track") return "none";
  if (standing.state === "no_recent_requests") return standing.attention ? "warning" : "neutral";
  return WARNED.includes(standing.state) ? "warning" : "neutral";
}

/** The badge: "Running out, act now" or "can wait", or the state's name; none on track. */
export function badgeOf(standing: Pick<SloStanding, "state" | "severity">): string | null {
  if (standing.state === "on_track") return null;
  if (standing.state === "running_out") {
    return standing.severity === "critical" ? "Running out, act now" : "Running out, can wait";
  }
  return STATE_NAMES[standing.state];
}

// FR12: each kind's own word, so a latency target never says "failures" (AC89).
const ALLOWED_NOUN: Record<string, string> = {
  service_latency: "allowed slow requests",
  check_availability: "allowed failed runs",
};
const HOURS_IN_TWO_DAYS = 48;
const HOURS_IN_A_DAY = 24;

export function allowedNoun(kind: string): string {
  return ALLOWED_NOUN[kind] ?? "allowed failures";
}

/** How long what is left lasts, in FR10's words; null when it is not known. */
export function lastsFor(hours: number | null): string | null {
  if (hours === null) return null;
  if (hours <= 0) return "Nothing is left.";
  if (hours < 1) return "What is left lasts under an hour.";
  if (hours < HOURS_IN_TWO_DAYS) return `What is left lasts about ${Math.floor(hours)} hours.`;
  return `What is left lasts about ${Math.floor(hours / HOURS_IN_A_DAY)} days.`;
}
