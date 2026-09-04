/**
 * Reading a group of exceptions: how many, and how long it has been going on.
 *
 * The screen's whole claim is that a hundred identical crashes are one line. What that
 * line has to answer beyond the count is "is this new, or has it been happening for a
 * week" — so first-seen against the window is the fact this module is built around.
 */

import { formatCount, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import type { TimeWindow } from "@/shared/selection";

import type { ErrorGroupSummary } from "./api";

/**
 * How close to the window's start still counts as "already happening when it opened".
 *
 * A fraction of the window rather than a fixed span: a minute is the whole story in a
 * fifteen-minute window and noise in a seven-day one.
 */
const WINDOW_EDGE_FRACTION = 0.02;

/**
 * Where a group started, as far as this window can tell.
 *
 * `before-window` is the honest answer, not a missing one. A group's `first_seen` is
 * the minimum over the rows *inside* the window, so a bug that has run for a week
 * reports the window's own start. Saying "started an hour ago" there would be a lie
 * the screen invented; saying "before this window" is what we actually know.
 */
export type GroupStart = "started-in-window" | "before-window";

export function groupStart(firstSeen: string, timeWindow: TimeWindow): GroupStart {
  const first = Date.parse(firstSeen);
  const start = Date.parse(timeWindow.from);
  const end = Date.parse(timeWindow.to);
  if (!Number.isFinite(first) || !Number.isFinite(start) || !Number.isFinite(end)) {
    return "before-window";
  }

  const edge = (end - start) * WINDOW_EDGE_FRACTION;
  return first - start > edge ? "started-in-window" : "before-window";
}

/**
 * A group that began inside the window is the one worth colour: it is an incident
 * starting now rather than a bug that was already there. Everything else on this
 * screen is an error too, so colouring all of it would mark nothing.
 */
export function isNewInWindow(group: ErrorGroupSummary, timeWindow: TimeWindow): boolean {
  return groupStart(group.first_seen, timeWindow) === "started-in-window";
}

export function totalOccurrences(groups: readonly ErrorGroupSummary[]): number {
  return groups.reduce((running, group) => running + group.occurrences, 0);
}

export function countNewInWindow(
  groups: readonly ErrorGroupSummary[],
  timeWindow: TimeWindow,
): number {
  return groups.filter((group) => isNewInWindow(group, timeWindow)).length;
}

/**
 * The sentence under the title. The grouping is the product, so the caption says both
 * numbers — how many distinct exceptions, and how many crashes they stand for.
 */
export function describeErrors(
  groups: readonly ErrorGroupSummary[],
  windowLabel: string,
  timeWindow: TimeWindow,
): string {
  if (groups.length === 0) return `Nothing has thrown in the last ${windowLabel}.`;

  const occurrences = totalOccurrences(groups);
  const opening =
    `${formatCount(groups.length)} distinct ${plural(groups.length, "exception")}` +
    ` · ${formatCount(occurrences)} ${plural(occurrences, "occurrence")}` +
    ` in the last ${windowLabel}`;

  const fresh = countNewInWindow(groups, timeWindow);
  return fresh === 0
    ? `${opening}. None of them started here.`
    : `${opening}, ${fresh} of which started inside it.`;
}

/** The services column: one name, or a count once the list stops being readable. */
const SERVICES_SHOWN = 2;

export function describeServices(services: readonly string[]): string {
  if (services.length === 0) return UNKNOWN;
  if (services.length <= SERVICES_SHOWN) return services.join(", ");
  return `${services[0]} +${services.length - 1}`;
}

/**
 * The newest instance that carried a stack trace.
 *
 * Not simply the newest: an exception recorded on a log record often arrives with a
 * type and a message and nothing else, and showing that one's empty stack while an
 * older instance holds a full one loses the only thing worth reading.
 */
export function firstWithStacktrace<T extends { stacktrace: string }>(
  occurrences: readonly T[],
): T | undefined {
  return occurrences.find((occurrence) => occurrence.stacktrace.trim() !== "");
}

export interface StackFrame {
  id: string;
  text: string;
}

/**
 * A stack trace as its lines, described up front rather than keyed by loop index.
 *
 * Each line is rendered as its own element so a frame too wide for the panel wraps
 * under a hanging indent instead of demanding a horizontal scroll. Measured at 26rem:
 * a Python traceback runs 697px against 383px of panel, so every line was scrolled.
 *
 * A frame has no identity of its own — two frames of a recursion are the same string —
 * so the description carries one, the way `Skeleton` describes its placeholder rows.
 */
export function stackFrames(stacktrace: string): StackFrame[] {
  const lines = stacktrace.replace(/\s+$/, "").split("\n");
  if (lines.length === 1 && lines[0] === "") return [];
  return lines.map((text, position) => ({ id: `frame-${position}`, text }));
}

export interface DescribedOccurrence<T> {
  id: string;
  occurrence: T;
}

/**
 * The recent instances, each given an identity.
 *
 * Nothing in an occurrence is one: under `OTEL_SEMCONV_EXCEPTION_SIGNAL_OPT_IN=logs/dup`
 * a single throw arrives as two records with the same service, trace, span and
 * timestamp. The sample is fixed newest-first and replaced whole, so position is stable
 * and is the only identity available.
 */
export function describeOccurrences<T>(occurrences: readonly T[]): Array<DescribedOccurrence<T>> {
  return occurrences.map((occurrence, position) => ({
    id: `occurrence-${position}`,
    occurrence,
  }));
}
