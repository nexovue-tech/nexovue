/**
 * Every word the reliability target screens say (F19 spec FR10, FR10a, FR12, FR13).
 *
 * The state itself is decided by the API, by the platform's one function, so a screen
 * and a message cannot disagree. Two things are decided here, in the browser: whether
 * the figures have gone stale (NFR7: a page whose refreshes fail must still stop
 * saying On track), and how a time reads in the project's zone (FR13).
 *
 * `test_slo_words.py` scans this folder for words the spec forbids (AC13).
 */

import type { SloQualifier, SloState, SloVariant, SloView } from "./api";

/** FR10: older than this, a figure is Not up to date, worked out here too (a guess). */
export const STALE_AFTER_MS = 15 * 60_000;
/** NFR7: the list and a target's page refetch this often (a guess, the job's own pace). */
export const REFRESH_MS = 60_000;

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

/** The design's tone table: words always, colour only beside them (rule 6). */
export type Tone = "critical" | "warning" | "neutral" | "none";

export function toneOf(view: Pick<SloView, "state" | "severity" | "attention">): Tone {
  if (view.state === "running_out") return view.severity === "critical" ? "critical" : "warning";
  if (view.state === "on_track") return "none";
  if (view.state === "no_recent_requests") return view.attention ? "warning" : "neutral";
  const warned: SloState[] = ["nothing_left", "source_gone", "not_up_to_date", "not_measured"];
  return warned.includes(view.state) ? "warning" : "neutral";
}

/** The badge: "Running out, act now" or "can wait", or the state's name. */
export function badgeOf(view: Pick<SloView, "state" | "severity">): string | null {
  if (view.state === "on_track") return null;
  if (view.state === "running_out") {
    return view.severity === "critical" ? "Running out, act now" : "Running out, can wait";
  }
  return STATE_NAMES[view.state];
}

/** NFR7, AC107: the state as this browser sees it now, whatever the last read said. */
export function liveState(view: SloView, now: number): { state: SloState; variant: SloVariant } {
  if (view.state === "disabled") return { state: view.state, variant: view.variant };
  const last = view.evaluated_minute ?? view.created_at;
  if (now - Date.parse(last) > STALE_AFTER_MS) {
    const wasBurning = view.state === "running_out" || view.qualifiers.includes("running_out");
    return { state: "not_up_to_date", variant: wasBurning ? "was_burning" : "plain" };
  }
  return { state: view.state, variant: view.variant };
}

/** FR13: a time in the project's zone, with its date when not today: "23:10 yesterday (IST)". */
export function timeIn(iso: string, zone: string, now: number = Date.now()): string {
  const at = new Date(iso);
  const clock = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: zone,
  }).format(at);
  const dayOf = (moment: Date) =>
    new Intl.DateTimeFormat("en-CA", { timeZone: zone, dateStyle: "short" }).format(moment);
  const zoneName = zoneAbbreviation(at, zone);
  const today = dayOf(new Date(now));
  const yesterday = dayOf(new Date(now - 86_400_000));
  const day = dayOf(at);
  let when = "";
  if (day === yesterday) when = " yesterday";
  else if (day !== today) {
    when = ` on ${new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: zone }).format(at)}`;
  }
  return `${clock}${when} (${zoneName})`;
}

/**
 * A zone's short name, such as "IST": English locales disagree on which zones have
 * one, so the first that is not a bare offset wins, and the offset otherwise.
 */
function zoneAbbreviation(at: Date, zone: string): string {
  const names = ["en-GB", "en-US", "en-IN"].map(
    (locale) =>
      new Intl.DateTimeFormat(locale, { timeZone: zone, timeZoneName: "short" })
        .formatToParts(at)
        .find((part) => part.type === "timeZoneName")?.value ?? zone,
  );
  return names.find((name) => !name.startsWith("GMT")) ?? names[0];
}

/** "3 h 10 m", "40 m", "2 d 4 h". */
export function durationOf(minutes: number): string {
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  if (days > 0) return hours > 0 ? `${days} d ${hours} h` : `${days} d`;
  if (hours > 0) return rest > 0 ? `${hours} h ${rest} m` : `${hours} h`;
  return `${rest} m`;
}

const grouped = new Intl.NumberFormat("en-GB");

/** FR12: "Failures still allowed: 312 of 672." */
export function allowanceSentence(view: SloView): string {
  return `${view.allowance_words}: ${grouped.format(view.figures.left)} of ${grouped.format(view.figures.allowed)}.`;
}

function percent(value: string | null): string {
  return value === null ? "—" : `${value}%`;
}

function objective(view: SloView): string {
  return `${Number(view.objective_percent)}%`;
}

/** FR10's sentence for the state this browser sees, in the project's zone. */
export function sentenceOf(view: SloView, zone: string, now: number = Date.now()): string {
  const { state, variant } = liveState(view, now);
  const quiet = view.quiet_minutes ?? 0;
  switch (state) {
    case "disabled":
      return `Alerts are off for this target. It keeps counting. ${allowanceSentence(view)}`;
    case "not_up_to_date": {
      const since = view.evaluated_minute ? timeIn(view.evaluated_minute, zone, now) : null;
      if (variant === "was_burning" && since) {
        return `Was using its allowance too fast when last worked out at ${since}. Not up to date since then.`;
      }
      return since
        ? `Not up to date since ${since}. These figures may be out of date.`
        : "Not worked out yet. These figures may be out of date.";
    }
    case "source_gone":
      return `${view.service_name ?? "This source"} has sent nothing in the last ${view.window_days} days.`;
    case "running_out":
      return "Using its allowance too fast.";
    case "counting":
      return `Reading the past ${view.window_days} days.`;
    case "not_measured":
      return `Nexovue measured only ${Math.floor((view.figures.coverage ?? 0) * 100)}% of this window. It will not judge it.`;
    case "no_recent_requests":
      return view.attention
        ? `No requests for ${durationOf(quiet)}. The figure below does not count that silence. Check the service is running.`
        : `No requests for ${durationOf(quiet)}. Its longest quiet stretch in the last 7 days was ${durationOf(view.longest_quiet_7d_minutes)}.`;
    case "building_history":
      return `Holds ${view.figures.days_held} of ${view.window_days} days. Too early to judge.`;
    case "nothing_left":
      return variant === "exactly_on_target"
        ? `No failures left. ${percent(view.figures.figure_percent)} against ${objective(view)}, exactly on the target. One more misses it.`
        : `Target missed: ${percent(view.figures.figure_percent)} against ${objective(view)}.`;
    case "too_little_traffic":
      return `At this traffic ${objective(view)} allows ${view.figures.allowed} failures. One failure moves it a lot.`;
    case "on_track":
      return allowanceSentence(view);
  }
}

/** FR10's next step, straight after the sentence; null where there is none. */
export function nextStepOf(state: SloState, view: Pick<SloView, "attention">): string | null {
  switch (state) {
    case "disabled":
      return "Turn alerts back on.";
    case "not_up_to_date":
      return "Nexovue is not judging this target. See system health.";
    case "source_gone":
      return "Point the target elsewhere, or delete it.";
    case "not_measured":
      return "Nothing to do; see system health.";
    case "no_recent_requests":
      return view.attention ? "Check the service is running." : null;
    case "building_history":
      return "Nothing to do yet.";
    case "nothing_left":
      return "It recovers as old failures leave the window.";
    case "too_little_traffic":
      return "Add a check-based target.";
    default:
      return null;
  }
}

/** A qualifier's line under the sentence (FR10). */
export function qualifierLine(qualifier: SloQualifier, view: SloView): string {
  switch (qualifier) {
    case "running_out":
      return "Using its allowance too fast, alerts are off.";
    case "not_up_to_date":
      return "These figures are not up to date.";
    case "no_alerts":
      return "No alerts will be sent.";
    case "snoozed":
      return "Messages are snoozed.";
    case "missed":
      return "Target missed.";
    case "thin_traffic":
      return "Traffic is thin: one failure moves this a lot.";
    case "not_measured":
      return `Nexovue measured only ${Math.floor((view.figures.coverage ?? 0) * 100)}%.`;
    case "no_recent_requests":
      return `No requests for ${durationOf(view.quiet_minutes ?? 0)}.`;
    case "building_history":
      return `Holds ${view.figures.days_held} of ${view.window_days} days. This figure will still move.`;
    case "never_failed":
      return "No failure was ever recorded. Check that your instrumentation sets span status.";
  }
}

/** FR10's order of qualifiers shows at most two; these two never collapse. */
export const QUALIFIERS_SHOWN = 2;
export const NEVER_COLLAPSED: readonly SloQualifier[] = ["not_up_to_date", "no_alerts"];

/** FR10a: the list's headline, which never says all is well. */
export function headlineOf(views: readonly SloView[], now: number = Date.now()): string {
  const live = views.map((view) => ({ view, ...liveState(view, now) }));
  const counted = live.filter(({ view, state }) => state === "not_up_to_date" || view.attention);
  const named = live.filter(({ state }) =>
    ["disabled", "building_history", "too_little_traffic", "counting"].includes(state),
  );
  const first = `${counted.length} of ${views.length} need attention.`;
  if (named.length === 0) return first;
  return `${first} ${named.length} cannot be judged yet or have alerts off.`;
}

/** FR10a's banner, while any figure is stale: "These figures are not up to date for 3 of 7 targets." */
export function staleBannerOf(views: readonly SloView[], now: number = Date.now()): string | null {
  const stale = views.filter((view) => liveState(view, now).state === "not_up_to_date").length;
  if (stale === 0) return null;
  return `These figures are not up to date for ${stale} of ${views.length} targets.`;
}

/** FR31's line on the list, for 30 days after a deletion. */
export function deletedLineOf(
  line: { name: string; deleted_at: string; purged_on: string; deleted_by_name: string | null },
  zone: string,
): string {
  const on = (iso: string) =>
    new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: zone }).format(
      new Date(iso),
    );
  const by = line.deleted_by_name ? ` by ${line.deleted_by_name}` : "";
  return `${line.name} was deleted${by} on ${on(line.deleted_at)}. Its daily figures go on ${on(line.purged_on)}.`;
}

/** FR31's confirmation: what goes, and when, before it can be confirmed (AC42). */
export function deleteWarningOf(daysHeld: number, purgedOn: string): string {
  return `Takes this target off the list. Its ${daysHeld} days of figures go on ${purgedOn}. It can be restored until then. Past months' results stay.`;
}

const FAILURE_LINE =
  "A failure is a request whose span status is ERROR. A 4xx usually does not count.";
const SAMPLE_LINE =
  "Needs server spans, not sampled before they reach nexovue. If you sample first, these figures count the sample.";
const REJECTED_LINE =
  "Requests rejected before they reach this service are not counted. Add a check for this.";
const MIXED_LINE = "A queue consumer and a web route are counted together in one service.";
/** FR4: the buckets do not separate failures. */
export const FAST_FAILURE_LINE =
  "A fast failure counts here. Pair this with an availability target.";

/** FR5's definition lines, by kind. */
export const AVAILABILITY_DEFINITION = [
  FAILURE_LINE,
  MIXED_LINE,
  SAMPLE_LINE,
  REJECTED_LINE,
  "Health checks count as good requests unless you leave them out.",
] as const;
export const LATENCY_DEFINITION = [
  FAST_FAILURE_LINE,
  "This includes health checks and background jobs, so it may not reflect your users' experience.",
  MIXED_LINE,
  SAMPLE_LINE,
  REJECTED_LINE,
] as const;
export const ENDPOINT_DEFINITION = [FAILURE_LINE, SAMPLE_LINE, REJECTED_LINE] as const;
export const CHECK_DEFINITION = [
  "Measured from nexovue's own check runs, one view from outside.",
  "A run counts only when it is the confirmed one: an attempt a retry overruled does not.",
  "Runs in maintenance windows are not counted, and runs that could not measure your service are not counted as failures.",
] as const;

export const DEFINITIONS = {
  service_availability: AVAILABILITY_DEFINITION,
  service_latency: LATENCY_DEFINITION,
  endpoint_availability: ENDPOINT_DEFINITION,
  check_availability: CHECK_DEFINITION,
} as const;

/** FR25a, AC79: said wherever no silence rule watches the service. */
export const STOPS_COMPLETELY = "This target does not alert if the service stops completely.";
export const ASK_WHO_CAN_ADD = "Ask an owner, admin or member to add one.";
/** FR16, AC79. */
export const CHECK_TOO_INFREQUENT =
  "Alerts cannot judge a check this infrequent. It needs at least 10 runs a day.";
export const SUGGESTION_FAILED = "Could not read this service's history. Enter a target yourself.";

/** A typed latency threshold's preview (FR3): the boundary it snaps down to. */
export function boundaryPreview(boundaryMs: number): string {
  return `This counts requests at or under ${boundaryMs.toFixed(1)} ms.`;
}
export function highestBoundaryLine(highestMs: number): string {
  return `The highest threshold nexovue counts is ${(highestMs / 1000).toFixed(1)} s.`;
}
export function aboveHighest(highestMs: number): string {
  return `Choose a threshold at or under ${(highestMs / 1000).toFixed(1)} s, the highest nexovue counts.`;
}
export function belowLowest(lowestMs: number): string {
  return `The finest threshold nexovue counts is ${lowestMs.toFixed(1)} ms.`;
}

/** The boundary at or under a typed threshold, from the platform's own boundaries; null
 * below the finest or above the highest (refused, AC101). */
export function snappedBoundary(askedMs: number, boundaries: readonly number[]): number | null {
  if (!Number.isFinite(askedMs) || boundaries.length === 0) return null;
  const highest = boundaries[boundaries.length - 1] ?? 0;
  if (askedMs > highest * (1 + 1e-9)) return null;
  let found: number | null = null;
  for (const boundary of boundaries) {
    if (boundary <= askedMs * (1 + 1e-9)) found = boundary;
  }
  return found;
}

/** FR12's allowance in whole events, in hundredths so no float rounds it up. */
export function allowedEvents(objective: string, events: number): number {
  const hundredths = Math.round(Number(objective) * 100);
  return Math.floor(((10_000 - hundredths) * events) / 10_000);
}

const INTERVAL_WORDS: Record<number, string> = {
  60: "every minute",
  300: "every 5 minutes",
  600: "every 10 minutes",
  1800: "every 30 minutes",
  3600: "every hour",
  21600: "every 6 hours",
  86400: "every day",
};
export function intervalWords(seconds: number): string {
  return INTERVAL_WORDS[seconds] ?? `every ${durationOf(Math.round(seconds / 60))}`;
}

/** FR8: what a check's interval allows, and what to change when that is too little:
 * "At every 5 minutes, 99.9% allows 8 failed runs over 28 days. Run it every minute or
 * choose 99.5%." */
export function checkAllowanceLine(input: {
  intervalSeconds: number;
  objective: string;
  windowDays: number;
  expectedRuns: number;
  fastestExpectedRuns: number;
  steps: readonly string[];
  /** The fewest failed runs a target may allow before one moves it a lot (FR10). */
  thinAllowed: number;
}): string {
  const allowed = allowedEvents(input.objective, input.expectedRuns);
  const runs = allowed === 1 ? "failed run" : "failed runs";
  const said = `At ${intervalWords(input.intervalSeconds)}, ${Number(input.objective)}% allows ${allowed} ${runs} over ${input.windowDays} days.`;
  if (allowed >= input.thinAllowed) return said;
  const faster =
    input.intervalSeconds > 60 &&
    allowedEvents(input.objective, input.fastestExpectedRuns) >= input.thinAllowed;
  const step = [...input.steps]
    .reverse()
    .find((candidate) => allowedEvents(candidate, input.expectedRuns) >= input.thinAllowed);
  const choices = [
    faster ? "Run it every minute" : null,
    step ? `choose ${Number(step)}%` : null,
  ].filter(Boolean);
  if (choices.length === 0) return said;
  const advice = choices.join(" or ");
  return `${said} ${advice.charAt(0).toUpperCase()}${advice.slice(1)}.`;
}

const weekPercent = (figure: string | null) => (figure === null ? "\u2014" : `${Number(figure)}%`);

/** FR7's arithmetic, in a sentence the person can check. */
export function suggestionSentence(input: {
  outcome: string;
  objective: string | null;
  worst: string | null;
  typical: string | null;
  boundaryMs: number | null;
  kindIsLatency: boolean;
}): string {
  const { outcome, objective, worst, typical } = input;
  if (outcome === "starter") {
    return "Fewer than 7 days of data. 99.5% is offered as a starting guess, not from your data.";
  }
  if (outcome === "below_lowest") {
    return `Your worst week was ${weekPercent(worst)} and your typical week ${weekPercent(typical)}, both below 90%, the lowest target nexovue sets. There is no suggestion.`;
  }
  if (outcome === "too_few") {
    return "Too few events to suggest a target: one failure would move it a lot.";
  }
  if (input.kindIsLatency && input.boundaryMs !== null) {
    return `In each of the last four weeks, 95% of requests took ${input.boundaryMs.toFixed(1)} ms or less. We suggest 95% at or under ${input.boundaryMs.toFixed(1)} ms.`;
  }
  if (outcome === "from_typical_week") {
    return `Your typical week was ${weekPercent(typical)}, your worst ${weekPercent(worst)}. We suggest ${weekPercent(objective)}. Your worst week would have missed this.`;
  }
  return `Your worst week of the last four was ${weekPercent(worst)}. We suggest ${weekPercent(objective)}.`;
}

/** The name the form proposes, from the source, like a message's label (D43). */
export function generatedName(input: {
  kind: string;
  service: string;
  endpoint: string;
  checkName: string;
  boundaryMs: number | null;
}): string {
  if (input.kind === "check_availability")
    return input.checkName ? `${input.checkName} availability` : "";
  if (!input.service) return "";
  if (input.kind === "endpoint_availability")
    return `${input.service} ${input.endpoint} availability`.trim();
  if (input.kind === "service_latency") {
    return input.boundaryMs === null
      ? `${input.service} latency`
      : `${input.service} latency under ${input.boundaryMs.toFixed(1)} ms`;
  }
  return `${input.service} availability`;
}

export const PRE_TICKED =
  "Pre-ticked because they look like health checks. Left-out requests count as neither good nor bad, and not as activity.";

export const SAMPLING_QUESTION = "Do you sample before sending to nexovue?";
export const SAMPLING_YES =
  "Then these figures count the sample and will be wrong. Alerts start off until this changes; a check-based target counts runs instead.";
export const SAMPLING_NOT_SURE =
  "Check your collector's sampling settings before relying on these figures.";
