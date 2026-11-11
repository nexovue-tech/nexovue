import { formatRelativeTime } from "@/shared/format/time";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { formatZonedTime } from "@/shared/format/zoned";
import { DASHBOARD_RANGES } from "@/shared/selection";

import type { DashboardContext, PanelReadResponse, StoredPanel } from "./api";
import { transient } from "./panel-words";

/**
 * What affects the whole page, said once, above the grid (F20, FR48, design *Page-level
 * states*): **at most one severe line at a time, in this order**, one state line, and the
 * context lines merged into one compact line. Pure, so each row of the priority table is
 * a test and none needs a canvas.
 *
 * The stale banner is the later, louder form of the outage line, never a second message.
 * Offline is the shell's strip and is not repeated here.
 */

const MS_PER_MINUTE = 60_000;
const MINUTES_PER_HOUR = 60;
/** Three missed refreshes make the page not current, at most five minutes (*guess*, FR31). */
export const STALE_AFTER_INTERVALS = 3;
export const STALE_AT_MOST_MS = 5 * MS_PER_MINUTE;
/** The newest telemetry older than this on a live page says the pipeline may be behind (FR54, *guess*). */
export const TELEMETRY_STALE_AFTER_MS = 10 * MS_PER_MINUTE;
/**
 * A fixed window that ends within this long of now holds minutes that are still arriving,
 * so its numbers can still change (AC28, *guess*: the settled-minute alert's own ten).
 */
export const RECENT_END_MS = 10 * MS_PER_MINUTE;
/** A first read slower than this says it is still reading (FR59, *guess*). */
export const STILL_READING_AFTER_MS = 3_000;
const MS_PER_SECOND = 1000;

export type LineAction =
  | "read-again"
  | "back-to-live"
  | "back-to-project"
  | "back-from-zoom"
  | "show-wider";

export interface Action {
  id: LineAction;
  label: string;
}

export type SevereKind =
  | "not-current"
  | "timeout"
  | "refused"
  | "outage"
  | "stale-telemetry"
  | "detection"
  | "still-reading";

export interface SevereLine {
  kind: SevereKind;
  words: string;
  /** Quoted to support, and selectable (FR60). */
  correlationId?: string | null;
  action?: Action;
}

export interface StateLine {
  kind: "picker" | "unknown-service" | "never-reported" | "quiet";
  words: string;
  action?: Action;
  /** A link for those who may act: where to start. */
  setup?: boolean;
}

export interface ContextSegment {
  kind: "link" | "fixed" | "zoomed";
  words: string;
  action: Action;
}

export interface PageLines {
  severe?: SevereLine;
  state?: StateLine;
  context: ContextSegment[];
}

export type BatchFailure = "refused" | "timed-out" | "failed";

export interface PageLineInput {
  now: number;
  zone: string;
  canSetUp: boolean;
  /** A live window refreshing at an interval: neither fixed, paused nor off. */
  refreshing: boolean;
  intervalMs: number | false;
  /** The page asked for a read and nothing has answered, since this moment. */
  readingSince: number | undefined;
  /** The whole batch failed, and how. */
  failure?: { kind: BatchFailure; correlationId?: string | null; retryScheduled?: boolean };
  answer?: PanelReadResponse;
  scanCount: number;
  lastGoodAt?: string;
  /** Back from a hidden tab: the banner waits for the first read (design, state table). */
  awaitingRead: boolean;
  context?: DashboardContext;
  /** The feed's own line, when detection has something to say (FR63). */
  detectionTitle?: string;
  projectName: string;
  /** The picker is unset on a dashboard that uses one (FR44, AC140). */
  pickerUnset: boolean;
  /** A link's `service=` the project has no record of: shown quoted, truncated, marked (AC123). */
  unknownService?: string;
  fromLink?: { own: string | undefined };
  /** "02:00 to 03:00", on the project's clock, when the window is fixed. */
  fixedTimes?: string;
  /** The fixed window ends in the last few minutes, which can still change (AC28). */
  endsRecently?: boolean;
  zoomed?: { before: string };
  /** The next range wider than the one shown, for a quiet window (FR48). */
  wider?: { label: string };
}

/** How long without a good reading makes the page not current: three intervals, at most five minutes. */
export function staleAfterMs(intervalMs: number): number {
  return Math.min(intervalMs * STALE_AFTER_INTERVALS, STALE_AT_MOST_MS);
}

/** The next range wider than a live one, for "Show the last 24 hours". */
export function widerRange(rangeId: string | undefined): { id: string; label: string } | undefined {
  const index = DASHBOARD_RANGES.findIndex((range) => range.id === rangeId);
  const next = index === -1 ? undefined : DASHBOARD_RANGES[index + 1];
  return next ? { id: next.id, label: `the last ${next.label}` } : undefined;
}

function sinceWords(lastGoodAt: string | undefined, zone: string, now: number): string {
  return lastGoodAt ? ` since ${formatZonedTime(lastGoodAt, { zone, now })}` : "";
}

/** "12 minutes", "3 hours": the age, in the words a person would say. */
function ageWords(ms: number): string {
  const minutes = Math.floor(ms / MS_PER_MINUTE);
  if (minutes < 2 * MINUTES_PER_HOUR) return `${minutes} minutes`;
  return `${Math.floor(minutes / MINUTES_PER_HOUR)} hours`;
}

/** The newest end of data among the time panels that read: how far behind the pipeline is (FR54). */
export function newestTelemetryAt(answer: PanelReadResponse): number | undefined {
  const ends = Object.values(answer.panels).flatMap((result) => {
    if (result.state !== "ok" || result.data_ends_at === null) return [];
    // `data_ends_at` is the start of the newest bucket, which holds data up to its end.
    return [Date.parse(result.data_ends_at) + result.bucket_seconds * MS_PER_SECOND];
  });
  return ends.length > 0 ? Math.max(...ends) : undefined;
}

function everyScanFailed(input: PageLineInput): boolean {
  if (!input.answer || input.scanCount === 0) return false;
  const results = Object.values(input.answer.panels).filter(
    (result) => result.state !== "unknown_kind",
  );
  return results.length > 0 && results.every(transient);
}

function severeLine(input: PageLineInput): SevereLine | undefined {
  const { failure, lastGoodAt, zone } = input;
  const since = sinceWords(lastGoodAt, zone, input.now);

  // Severe 2: no scan panel read for three intervals, on a page that should be refreshing.
  if (
    input.refreshing &&
    input.intervalMs !== false &&
    lastGoodAt !== undefined &&
    !input.awaitingRead &&
    input.now - Date.parse(lastGoodAt) >= staleAfterMs(input.intervalMs)
  ) {
    return {
      kind: "not-current",
      words: `NOT CURRENT. Last good reading ${formatZonedTime(lastGoodAt, { zone, now: input.now })}`,
      correlationId: failure?.correlationId ?? input.answer?.correlation_id,
      action: { id: "read-again", label: "Read again" },
    };
  }
  if (failure?.kind === "timed-out") {
    return {
      kind: "timeout",
      words: "The page did not answer within 25 seconds.",
      correlationId: failure.correlationId,
      action: { id: "read-again", label: "Read again" },
    };
  }
  if (failure?.kind === "refused") {
    return {
      kind: "refused",
      words: "Too many reads just now.",
      correlationId: failure.correlationId,
      // A read already waiting on the server's own wait is not offered a second (AC145).
      action: failure.retryScheduled ? undefined : { id: "read-again", label: "Read again" },
    };
  }
  if (failure?.kind === "failed" || everyScanFailed(input)) {
    return {
      kind: "outage",
      words: `Could not read telemetry${since}. Notes and findings below are current.`,
      correlationId: failure?.correlationId ?? input.answer?.correlation_id,
      action: { id: "read-again", label: "Read again" },
    };
  }

  // Severe 3: reads succeed and the newest telemetry is old, or detection is not judging.
  if (input.refreshing && input.answer) {
    const newest = newestTelemetryAt(input.answer);
    if (newest !== undefined && input.now - newest > TELEMETRY_STALE_AFTER_MS) {
      return {
        kind: "stale-telemetry",
        words: `Newest telemetry here is ${ageWords(input.now - newest)} old. The pipeline may be behind, or nothing is sending`,
      };
    }
  }
  if (input.detectionTitle) return { kind: "detection", words: input.detectionTitle };

  // Severe 4: a first read still going.
  if (
    input.readingSince !== undefined &&
    input.now - input.readingSince >= STILL_READING_AFTER_MS
  ) {
    return {
      kind: "still-reading",
      words: "Still reading. This can take up to 25 seconds",
    };
  }
  return undefined;
}

const SERVICE_SHOWN_MAX = 40;

/** A name from a link, cut so it cannot fill the line, with the cut marked. */
function truncated(name: string): string {
  return name.length > SERVICE_SHOWN_MAX ? `${name.slice(0, SERVICE_SHOWN_MAX)}…` : name;
}

/** Panels that read a picked service and have no value for it. */
export function usesPicker(panel: StoredPanel): boolean {
  const service = panel.settings.service;
  return typeof service === "object" && service !== null;
}

function stateLine(input: PageLineInput): StateLine | undefined {
  const { context, projectName } = input;
  if (input.pickerUnset) {
    return { kind: "picker", words: "Choose a service to fill this dashboard" };
  }
  if (input.unknownService !== undefined) {
    return {
      kind: "unknown-service",
      words: `“${truncated(visibleEscapes(input.unknownService))}” from this link was not found in ${visibleEscapes(projectName)}.`,
    };
  }
  if (context && !context.ever_reported) {
    const name = visibleEscapes(projectName);
    return input.canSetUp
      ? { kind: "never-reported", words: `Nothing has reported to ${name} yet.`, setup: true }
      : {
          kind: "never-reported",
          words: `Nobody has connected ${name} yet. An owner or admin can set it up.`,
        };
  }
  if (context && input.answer && input.scanCount > 0) {
    const results = Object.values(input.answer.panels).filter(
      (result) => result.state !== "unknown_kind",
    );
    if (results.length > 0 && results.every((result) => result.state === "empty")) {
      const seen = context.last_seen_at
        ? ` Last seen ${formatRelativeTime(context.last_seen_at, input.now)}.`
        : "";
      return {
        kind: "quiet",
        words: `Nothing reported in this window.${seen}`,
        action: input.wider ? { id: "show-wider", label: `Show ${input.wider.label}` } : undefined,
      };
    }
  }
  return undefined;
}

function contextSegments(input: PageLineInput, projectLabel: string): ContextSegment[] {
  const segments: ContextSegment[] = [];
  const stillChanging = input.endsRecently ? " Its newest minutes can still change." : "";
  if (input.fromLink) {
    const own = input.fromLink.own ? ` You were on ${input.fromLink.own}.` : "";
    segments.push({
      kind: "link",
      words: `Showing ${projectLabel} from this link.${own}`,
      action: {
        id: "back-to-project",
        label: input.fromLink.own ? `Back to ${input.fromLink.own}` : "Use my own project",
      },
    });
  }
  if (input.zoomed) {
    segments.push({
      kind: "zoomed",
      words: `Zoomed to ${input.fixedTimes ?? "these times"}.${stillChanging}`,
      action: { id: "back-from-zoom", label: `Back to ${input.zoomed.before}` },
    });
  } else if (input.fixedTimes) {
    segments.push({
      kind: "fixed",
      words: `Fixed time: ${input.fixedTimes}.${stillChanging}`,
      action: { id: "back-to-live", label: "Back to live" },
    });
  }
  return segments;
}

export function pageLines(input: PageLineInput): PageLines {
  return {
    severe: severeLine(input),
    state: stateLine(input),
    context: contextSegments(input, visibleEscapes(input.projectName)),
  };
}
