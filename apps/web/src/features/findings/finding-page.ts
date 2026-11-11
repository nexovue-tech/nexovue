import type { ChartBand } from "@/shared/components/chart-band";
import type { PlotRows } from "@/shared/components/TrendChart";
import { formatDuration } from "@/shared/format/duration";
import { formatPercent, UNKNOWN } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";

import { formatGap, formatRelativeTime } from "@/shared/format/time";
import { formatZonedTime } from "@/shared/format/zoned";

import type {
  CasePeople,
  EarlierCase,
  EmailOutcome,
  Finding,
  FindingChanged,
  FindingDetail,
  FindingSignal,
  FindingSignalKind,
  NewNormalPreview,
  ReleaseContext,
  ReleaseFact,
  SignalSeries,
  TimelineEvent,
} from "./api";
import { CLOSED_AS, NOBODY_HAS_TAKEN_THIS, resolutionWords } from "./finding-words";
import { issueEventSentence } from "./link-words";

/**
 * What a finding's page says and offers (FR31, design *The finding page*), apart from
 * how it is drawn, so each rule can be read and tested on its own.
 */

const MS_PER_SECOND = 1000;
const PERCENT = 100;

export const SIGNAL_NAMES: Record<FindingSignalKind, string> = {
  slower: "Slower",
  failing: "Failing more",
  traffic: "Losing traffic",
};

const RATE_UNIT = "requests a minute";

/** A value in its signal's unit; a missing one is the em dash, never a zero. */
export function formatSignalValue(unit: FindingSignal["unit"], value: number | null): string {
  if (value === null) return UNKNOWN;
  if (unit === "ms") return formatDuration(value);
  if (unit === "%") return formatPercent(value / PERCENT);
  return formatRatePerMinute(value);
}

/** "412 ms at worst, against 110 ms normally. Now 330 ms." */
export function describeSignal(entry: FindingSignal): string {
  const value = (reading: number | null) => formatSignalValue(entry.unit, reading);
  const counted = entry.unit === "rpm" ? ` ${RATE_UNIT}` : "";
  return `${value(entry.worst)}${counted} at worst, against ${value(entry.normal)} normally. Now ${value(entry.current)}.`;
}

/**
 * The endpoint a signal belongs to, for its heading: only an endpoint's own entry. A
 * service's entry may name an endpoint as evidence, which the evidence line shows.
 */
export function ownEndpoint(entry: FindingSignal): string | null {
  if (!entry.key) return entry.endpoint;
  return entry.key.startsWith("endpoint:") ? entry.endpoint : null;
}

/**
 * The evidence endpoint against the same time last week (FR3), or null when the signal
 * names none: "POST /pay: p95 400ms, against 100ms the same time last week."
 */
export function evidenceSentence(entry: FindingSignal): string | null {
  if (!entry.endpoint) return null;
  const now = formatSignalValue(entry.unit, entry.endpoint_current);
  const before = formatSignalValue(entry.unit, entry.endpoint_last_week);
  const reading = entry.signal === "failing" ? `${now} failing` : `p95 ${now}`;
  return `${entry.endpoint}: ${reading}, against ${before} the same time last week.`;
}

/** The band the finding was judged by, as stored with it (FR16): "Normal band 110ms to 165ms." */
export function bandSentence(entry: FindingSignal): string {
  if (entry.band_low === null && entry.band_high === null) return `Normal band ${UNKNOWN}.`;
  const low = formatSignalValue(entry.unit, entry.band_low);
  const high = formatSignalValue(entry.unit, entry.band_high);
  return `Normal band ${low} to ${high}${entry.unit === "rpm" ? ` ${RATE_UNIT}` : ""}.`;
}

/** Who may do what to this finding, as this person (FR22, FR23, AC39). */
export interface FindingActions {
  falseAlarm: boolean;
  newNormal: boolean;
  snooze: boolean;
  mute: boolean;
}

export function availableActions(
  finding: FindingDetail,
  { canOperate, canConfigure }: { canOperate: boolean; canConfigure: boolean },
): FindingActions {
  const open = finding.state !== "closed";
  const ofAService = finding.service_name !== null;
  return {
    // D47: Acknowledge is gone from the console — **Take this** (the case) is
    // the one button, and the API route stays for callers that want only it.
    // A closed finding keeps only this, and only if it recovered: it may still have
    // been nothing (design, *closed*).
    falseAlarm: canOperate && (open || finding.closed_reason === "recovered"),
    newNormal: canConfigure && open && ofAService,
    // The 3 a.m. lever, every working role's (D25); a mute is an admin's.
    snooze: canOperate && open && ofAService,
    mute: canConfigure && open && ofAService,
  };
}

/**
 * The dangerous action states its effect in real numbers before it can be confirmed:
 * "checkout's normal becomes about 330 ms, from 110 ms."
 */
export function newNormalSentence(service: string, preview: NewNormalPreview): string {
  const parts = preview.signals.map((shift) => {
    const after = formatSignalValue(shift.unit, shift.normal_after);
    const before = formatSignalValue(shift.unit, shift.normal_before);
    const what = shift.signal === "traffic" ? "normal traffic" : "normal";
    return `${service}'s ${what} becomes about ${after}, from ${before}`;
  });
  return `${parts.join("; ")}.`;
}

/** Why a new normal cannot be set yet: the level is not known well enough. */
export function notYetSentence(preview: NewNormalPreview): string {
  return `Not yet: the new level is known from ${preview.readings} of the ${preview.needed} readings it needs. Try again in a few minutes.`;
}

export type DoneAction = "false-alarm" | "new-normal";

/** What happened, and where it went (design principle 3, *Success*). */
export function successSentence(
  action: DoneAction,
  changed: FindingChanged,
  { wasOpen }: { wasOpen: boolean },
): string {
  if (!wasOpen) return "Marked a false alarm. It had already closed, so no message was sent.";
  const closed =
    action === "false-alarm"
      ? "Closed as a false alarm."
      : "Closed as the new normal. Judging goes on at the new level.";
  const told =
    changed.resolved_to.length > 0
      ? `Resolved message sent to ${changed.resolved_to.join(", ")}.`
      : "No channel had been told of it, so no message was sent.";
  return `${closed} ${told}`;
}

/** One signal's chart: the readings, the normal, and the band's edge, on one scale. */
export interface SignalPlot {
  signal: FindingSignalKind;
  unit: FindingSignal["unit"];
  data: PlotRows;
  /** The normal band, filled between the normal and its edge (AC38). */
  band: ChartBand;
}

export const PLOT_LINES = ["This finding", "Normal", "Edge of the normal band"] as const;

// `data`'s rows: the x axis, then each of `PLOT_LINES` in order.
const NORMAL_ROW = 2;
const EDGE_ROW = 3;

export function signalPlot(series: SignalSeries): SignalPlot {
  // Losing traffic is judged below its normal; the other signals above it.
  const band =
    series.signal === "traffic"
      ? { upper: NORMAL_ROW, lower: EDGE_ROW }
      : { upper: EDGE_ROW, lower: NORMAL_ROW };
  return {
    signal: series.signal,
    unit: series.unit,
    band,
    data: [
      series.points.map((point) => Date.parse(point.at) / MS_PER_SECOND),
      series.points.map((point) => point.value),
      series.points.map((point) => point.normal),
      series.points.map((point) => point.edge),
    ],
  };
}

// ---------------------------------------------------------------- the case

/**
 * The case half of the page (F22 T1.11): every sentence from the spec's tables,
 * which actions this person gets, and the line after each act — rules apart from
 * drawing, so each row of the state table can be read and tested on its own.
 */

/** What the case sentence reads: a page's finding or a person's feed row. */
export type CaseFacts = Finding & CasePeople;

export interface CaseView {
  /** The row the page shows. */
  finding: CaseFacts;
  /** The case row: the root when the page's finding joined one, else the same. */
  root: CaseFacts;
  viewerId: string | null;
  zone?: string;
  now?: number;
}

const time = (iso: string | null | undefined, zone?: string, now?: number) =>
  iso ? formatZonedTime(iso, { zone, now }) : "";

function investigatedBy(root: CaseFacts, viewerId: string | null) {
  if (root.assignee_id == null) return "still being investigated, nobody assigned";
  const name = root.assignee_id === viewerId ? "you" : (root.assignee_name ?? "Former member");
  return `still being investigated by ${name}`;
}

function howItEnded(root: CaseFacts, zone?: string, now?: number): string {
  if (root.case_resolution == null) return "";
  return `${resolutionWords(root.case_resolution, {
    by: root.case_resolved_by_name,
    at: time(root.case_resolved_at, zone, now),
  })}.`;
}

function signalWords(finding: CaseFacts, zone?: string, now?: number): string {
  if (finding.state !== "closed") return `Open since ${time(finding.started_at, zone, now)}`;
  const how = finding.closed_reason ? CLOSED_AS[finding.closed_reason] : "Closed";
  return finding.closed_at ? `${how} at ${time(finding.closed_at, zone, now)}` : how;
}

/**
 * The page's one sentence stating both questions (FR23, AC5, AC63, AC100,
 * AC107): the signal's state in the closed reason's own words, and the case's,
 * each time through the zoned formatter.
 */
export function caseSentence({ finding, root, viewerId, zone, now }: CaseView): string {
  const signal = signalWords(finding, zone, now);
  if (finding.root_finding_id != null && finding.id !== root.id) {
    // The joined finding's page leads with the case elsewhere (FR12); its own
    // lead line is built by `joinedLeadLine`.
    return signal;
  }
  if (root.case_state == null) {
    return finding.state === "closed" ? signal : `${signal}. ${NOBODY_HAS_TAKEN_THIS}`;
  }
  if (root.case_state === "resolved") {
    const still =
      root.case_signal === "open" ? "Still open: the signal has not recovered." : `${signal}.`;
    return `${still} ${howItEnded(root, zone, now)}`.trim();
  }
  // Investigating. Never "Recovered" as the current state while a joined
  // finding is open (AC59): the open return leads.
  if (finding.state === "closed" && root.open_joined_finding_id != null) {
    const back = `Came back at ${time(root.open_joined_since, zone, now)} and is still open`;
    if (root.assignee_id == null) return `${back}. Being investigated, nobody assigned`;
    const name = root.assignee_id === viewerId ? "You" : (root.assignee_name ?? "Former member");
    if (root.assignee_acted_since_join === false) {
      const ago = root.case_taken_at ? formatRelativeTime(root.case_taken_at) : "earlier";
      return `${back}. ${name} took the case ${ago}, and has not acted since`;
    }
    return `${back}. ${name} took the case at ${time(root.case_taken_at, zone, now)}`;
  }
  if (root.assignee_id == null) {
    const working =
      finding.state === "closed"
        ? investigatedBy(root, viewerId)
        : "Being investigated, nobody assigned";
    return `${signal}${finding.state === "closed" ? ", " : ". "}${working}`;
  }
  if (finding.state === "closed") {
    return `${signal}, ${investigatedBy(root, viewerId)}`;
  }
  const name = root.assignee_id === viewerId ? "You" : (root.assignee_name ?? "Former member");
  return `${signal}. ${name} took this at ${time(root.case_taken_at, zone, now)}`;
}

/** The joined page's lead (FR12): where the case is, and what moved here. */
export function joinedLeadLine(root: CaseFacts, zone?: string, now?: number): string {
  if (root.case_state === "resolved") {
    return `Part of a case resolved on ${time(root.case_resolved_at, zone, now)}`;
  }
  return `Part of a case that started at ${time(root.case_opened_at, zone, now)}`;
}

/** Which case actions this person gets (design, *State table*). */
export interface CaseActions {
  takeThis: boolean;
  /** The name to take over from; the secondary act on someone else's case. */
  takeOverFrom: string | null;
  resolve: boolean;
  reassign: boolean;
  reopen: boolean;
  note: boolean;
  /** The earlier case **Reopen that case** reopens, beside Take this (FR13, AC95). */
  reopenEarlier: string | null;
}

const NO_CASE_ACTIONS: CaseActions = {
  takeThis: false,
  takeOverFrom: null,
  resolve: false,
  reassign: false,
  reopen: false,
  note: false,
  reopenEarlier: null,
};

export function caseActions(
  root: FindingDetail,
  { canOperateCases, viewerId }: { canOperateCases: boolean; viewerId: string | null },
): CaseActions {
  if (!canOperateCases) return NO_CASE_ACTIONS;
  if (root.case_state == null) {
    const earlier = root.earlier_case;
    return {
      ...NO_CASE_ACTIONS,
      takeThis: true,
      note: true,
      reopenEarlier: earlier != null && earlierCaseLine(earlier).reopenable ? earlier.id : null,
    };
  }
  if (root.case_state === "resolved") return { ...NO_CASE_ACTIONS, reopen: true, note: true };
  const mine = root.assignee_id != null && root.assignee_id === viewerId;
  const someoneElses = root.assignee_id != null && !mine;
  return {
    takeThis: root.assignee_id == null,
    takeOverFrom: someoneElses ? (root.assignee_name ?? "Former member") : null,
    resolve: true,
    reassign: true,
    reopen: false,
    note: true,
    reopenEarlier: null,
  };
}

/** "at 21:00 UTC" for an instant today, "on 16 Nov, 18:00 UTC" for another day (FR50). */
function whenWords(iso: string, zone?: string, now?: number): string {
  const said = time(iso, zone, now);
  return TODAY_ONLY.test(said) ? `at ${said}` : `on ${said}`;
}
const TODAY_ONLY = /^\d{2}:\d{2} /;
const DAY_MS = 86_400_000;

/**
 * When an active case ends by itself, in the spec's words (FR26, FR49), or null
 * when the server names no end: it sets one only while the screen should say so.
 */
export function caseEndingSentence(root: CaseFacts, zone?: string, now?: number): string | null {
  if (root.case_ends_on == null || root.case_ends_by == null) return null;
  const ends = whenWords(root.case_ends_on, zone, now);
  if (root.case_ends_by === "untouched") {
    return `Recovered. Ends by itself ${ends} unless someone adds something.`;
  }
  const quiet =
    root.case_activity_at == null
      ? null
      : Math.floor(((now ?? Date.now()) - Date.parse(root.case_activity_at)) / DAY_MS);
  const days = quiet == null ? "" : `No activity for ${quiet} days. `;
  return `${days}Ends by itself ${ends}. Add a note to keep it going, or resolve it.`;
}

/** An earlier case ended by a person's own decision, which only a link names (AC95). */
const PERSON_ENDED = new Set(["resolved", "false_alarm", "new_normal"]);

/**
 * The service's earlier case, named on a finding that holds none (FR9, FR13):
 * a quiet active one taking this joins, or a resolved one. One that ended
 * without a person's decision can be reopened from here.
 */
export function earlierCaseLine(
  earlier: EarlierCase,
  zone?: string,
  now?: number,
): { sentence: string; reopenable: boolean } {
  if (earlier.case_state === "investigating") {
    return {
      sentence: `This service has a case nobody has touched since ${time(earlier.case_activity_at, zone, now)}`,
      reopenable: false,
    };
  }
  const resolvedAt = time(earlier.case_resolved_at, zone, now);
  const resolution = earlier.case_resolution;
  if (resolution == null || PERSON_ENDED.has(resolution)) {
    return { sentence: `This service had a case resolved on ${resolvedAt}`, reopenable: false };
  }
  return {
    sentence: `This service had a case that ended on ${resolvedAt}. ${resolutionWords(resolution, {
      at: resolvedAt,
    })}`,
    reopenable: true,
  };
}

/** How long a case is kept (FR24, FR27, AC112), or null when there is nothing to say. */
export function keptSentence(root: FindingDetail, zone?: string, now?: number): string | null {
  const parts: string[] = [];
  if (root.deletes_on != null) parts.push(`Kept until ${time(root.deletes_on, zone, now)}.`);
  const kept = root.joined_kept_count ?? 0;
  if (kept > 0) {
    parts.push(
      kept === 1 ? "1 later finding is still kept." : `${kept} later findings are still kept.`,
    );
  }
  return parts.length > 0 ? parts.join(" ") : null;
}

export type CaseAction = "take" | "assign" | "unassign" | "resolve" | "reopen";

/**
 * The line after an act, each true to what the system knows (spec, *Words after
 * an action*; FR48, AC98): the PagerDuty lines show only when the project has a
 * PagerDuty channel.
 */
export function caseDoneSentence(
  action: CaseAction,
  {
    hasPagerDuty = false,
    assigneeName,
    emailOutcome,
  }: { hasPagerDuty?: boolean; assigneeName?: string | null; emailOutcome?: EmailOutcome } = {},
): string {
  if (action === "take") {
    return hasPagerDuty
      ? "Yours. PagerDuty is not told: acknowledge the page there too."
      : "Yours.";
  }
  if (action === "assign") {
    const name = assigneeName ?? "them";
    if (emailOutcome === "will_try") {
      return `Assigned to ${name}. An email is on its way, so check they saw it if it is urgent.`;
    }
    if (emailOutcome === "rate_limited") {
      return `Assigned to ${name}. ${name} was emailed about this case a moment ago.`;
    }
    if (emailOutcome === "not_configured") {
      return `Assigned to ${name}. Email is not set up here, so tell them yourself.`;
    }
    return `Assigned to ${name}.`;
  }
  if (action === "unassign") return "Assigned to nobody.";
  if (action === "reopen") return "Reopened.";
  return hasPagerDuty
    ? "Resolved. PagerDuty is not told, so a live page stays until the signal recovers."
    : "Resolved.";
}

/** One timeline row's words (FR16), from kinds and typed detail alone. */
export function eventSentence(event: TimelineEvent): string {
  const who = event.actor_kind === "person" ? (event.actor_name ?? "Former member") : null;
  const detail = event.detail ?? {};
  switch (event.kind) {
    case "finding_opened":
      return `Opened (${String(detail.severity ?? "warning")})`;
    case "severity_raised":
      return "Became critical";
    case "signal_joined":
      return "Another signal joined this finding";
    case "finding_closed": {
      const reason = detail.closed_reason as keyof typeof CLOSED_AS | undefined;
      return reason && reason in CLOSED_AS ? CLOSED_AS[reason] : "Closed";
    }
    case "flapping_started":
      return "This service started flapping; openings and closings are not listed one by one";
    case "case_started":
      return `${who ?? "The system"} started the case`;
    case "acknowledged":
      return `${who ?? "Someone"} acknowledged this`;
    case "assigned":
      return who != null && event.actor_id === detail.assignee_id
        ? `${who} took this`
        : `${who ?? "The system"} reassigned the case`;
    case "unassigned":
      return who == null ? "The assignment was cleared" : `${who} cleared the assignment`;
    case "case_resolved": {
      const resolution = detail.resolution;
      if (resolution === "resolved") return `${who ?? "Someone"} resolved the case`;
      return typeof resolution === "string"
        ? // biome-ignore lint/suspicious/noExplicitAny: narrowed by the writer's enum check.
          resolutionWords(resolution as any)
        : "The case resolved";
    }
    case "case_reopened":
      return `${who ?? "The system"} reopened the case`;
    case "false_alarm":
      return `${who ?? "Someone"} marked this a false alarm`;
    case "new_normal":
      return `${who ?? "Someone"} accepted this as the new normal`;
    case "note_added":
      return event.note_body == null
        ? `${who ?? "Someone"} wrote a note`
        : `${who ?? "Someone"}: ${event.note_body}`;
    case "note_deleted":
      return "A note was deleted";
    case "finding_joined_case":
      return "A returning finding joined this case";
    case "finding_left_case":
      return "An open finding left this case and stands on its own";
    case "explained":
      return "The explanation joined this finding";
    default:
      return issueEventSentence(event) ?? event.kind.replaceAll("_", " ");
  }
}

/* ------------------------------------------- F28: the release before this (FR32) */

/** Beside every row that names a release that came before (FR32): order, not cause. */
export const CAME_FIRST = "It came first. That is not evidence that it caused this.";
/** Within this, a release is stated plainly with a comparison to open; older, dimmer. */
const STATED_PLAINLY_WITHIN_MS = 24 * 60 * 60 * MS_PER_SECOND;
/** A project finding lists this many services' releases, then how many more. */
const AFFECTED_SHOWN = 5;
const NEARBY_HOURS = 2;

const BASIS_WORDS: Record<ReleaseFact["basis"], string> = {
  both: "Reported by your CI, seen in traffic.",
  seen: "Seen in traffic.",
  reported: "Reported by your CI.",
};

const STATE_WORDS: Partial<Record<NonNullable<ReleaseFact["last_state"]>, string>> = {
  failed: "CI reported it failed.",
  rolled_back: "CI reported it rolled back.",
};

/** One sentence of the *Release before this* section, by the design's wording table. */
export interface ReleaseLine {
  text: string;
  /** An older release reads dimmer. */
  dim: boolean;
  /** Whether "It came first…" goes beside it: only when it names an earlier release. */
  cameFirst: boolean;
  /** Whether to offer the comparison: only under 24 hours. */
  compare: boolean;
}

/** "10 Nov 2026" on the project's calendar. */
function dayIn(iso: string, zone: string | undefined): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return UNKNOWN;
  const options: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" };
  try {
    return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: zone }).format(at);
  } catch {
    return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" }).format(at);
  }
}

const said = (text: string, cameFirst: boolean, extra: Partial<ReleaseLine> = {}): ReleaseLine => ({
  text,
  dim: false,
  cameFirst,
  compare: false,
  ...extra,
});

/**
 * What came before a service finding (design, *Finding page*): the release before it at
 * any age, or why none can be named. Only a row naming an earlier release carries
 * `CAME_FIRST`; nothing before, already running, coverage none and first heard after
 * do not, since no release came first or none is known to have.
 */
export function releaseBefore(
  finding: Pick<FindingDetail, "started_at" | "release_context">,
  { zone }: { zone?: string } = {},
): ReleaseLine {
  const context = finding.release_context;
  const release = context.before;
  if (context.first_heard_at !== null) {
    return said(
      `We first heard about deploys on this service on ${dayIn(context.first_heard_at, zone)}, after this started.`,
      false,
    );
  }
  if (context.coverage === "none") {
    return said("We receive no deploy information for this service.", false);
  }
  if (release === null) {
    const age =
      context.last_reported_at === null
        ? undefined
        : formatGap(context.last_reported_at, finding.started_at);
    const last = age === undefined ? "" : ` Last report: ${age} before this.`;
    return said(
      `No release of this service before this started, from what we can see.${last}`,
      false,
    );
  }
  if (release.already_running) {
    return said(
      `${release.version} was running when we started counting on ${dayIn(release.started_at, zone)}. No release since.`,
      false,
    );
  }
  const gap = formatGap(release.started_at, finding.started_at) ?? UNKNOWN;
  const elapsed = Date.parse(finding.started_at) - Date.parse(release.started_at);
  if (elapsed >= STATED_PLAINLY_WITHIN_MS) {
    return said(`The last known release was ${gap} earlier.`, true, { dim: true });
  }
  if (release.is_return) {
    return said(`${release.version} came back ${gap} before this.`, true, { compare: true });
  }
  if (release.basis === "reported") {
    return said(
      `${release.version} was reported ${gap} before this, and we have not seen it in traffic.`,
      true,
      { compare: true },
    );
  }
  const state = release.last_state === null ? undefined : STATE_WORDS[release.last_state];
  return said(
    [
      `${release.version} of ${release.service_name} started ${gap} before this.`,
      BASIS_WORDS[release.basis],
      state,
    ]
      .filter((part) => part !== undefined)
      .join(" "),
    true,
    { compare: true },
  );
}

/** A project finding's list: each affected service's latest release, up to five. */
export function affectedReleases(context: ReleaseContext): {
  rows: ReleaseFact[];
  more: string | null;
  cameFirst: boolean;
} {
  const rows = context.affected.slice(0, AFFECTED_SHOWN);
  const hidden = context.affected.length - rows.length;
  return { rows, more: hidden > 0 ? `and ${hidden} more` : null, cameFirst: rows.length > 0 };
}

/**
 * Releases elsewhere in the project in the 2 hours before (FR52), each marked when this
 * service calls it. With none on this service, a sentence counts the others.
 */
export function elsewhere(
  context: ReleaseContext,
  service: string,
): { lead: string | null; rows: ReleaseFact[] } {
  const rows = context.nearby.filter((fact) => fact.service_name !== service);
  const ownNearby = context.nearby.some((fact) => fact.service_name === service);
  const lead =
    !ownNearby && rows.length > 0
      ? `No release on this service. ${rows.length} elsewhere in this project in the last ${NEARBY_HOURS} hours.`
      : null;
  return { lead, rows };
}

/** The release page's search, carrying the finding so its window reaches it (FR22). */
export function comparisonSearch(
  fact: ReleaseFact,
  finding: Pick<FindingDetail, "id" | "started_at">,
): { period: string; finding: string; finding_at: string } {
  return { period: fact.period_id, finding: finding.id, finding_at: finding.started_at };
}
