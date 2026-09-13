/**
 * Reading a group of exceptions: how many, and how long it has been going on.
 *
 * The screen's whole claim is that a hundred identical crashes are one line. What that
 * line has to answer beyond the count is "is this new, or has it been happening for a
 * week" — so first-seen against the window is the fact this module is built around.
 */

import { formatCount, UNKNOWN } from "@/shared/format/number";
import { plural } from "@/shared/format/plural";
import { formatGap } from "@/shared/format/time";

import type { ErrorGroupSummary } from "./api";

/**
 * A group that began inside the window is the one worth colour: it is an incident
 * starting now rather than a bug that was already there. Everything else on this
 * screen is an error too, so colouring all of it would mark nothing.
 *
 * **The API decides this, not the screen.** `first_seen` is the minimum over the
 * rows *inside* the window, so a bug that has run for a week reports the
 * window's own start and a margin is what tells the two apart. That margin used
 * to live here, and the "only new" filter needs the same one — two copies of it
 * would eventually disagree, and the visible symptom would be a filter hiding a
 * row the list had just marked as new.
 */
export function isNewInWindow(group: ErrorGroupSummary): boolean {
  return group.started_in_window;
}

export function totalOccurrences(groups: readonly ErrorGroupSummary[]): number {
  return groups.reduce((running, group) => running + group.occurrences, 0);
}

export function countNewInWindow(groups: readonly ErrorGroupSummary[]): number {
  return groups.filter(isNewInWindow).length;
}

/**
 * The sentence under the title. The grouping is the product, so the caption says both
 * numbers — how many distinct exceptions, and how many crashes they stand for.
 */
export function describeErrors(groups: readonly ErrorGroupSummary[], windowLabel: string): string {
  if (groups.length === 0) return `Nothing has thrown in the last ${windowLabel}.`;

  const occurrences = totalOccurrences(groups);
  const opening =
    `${formatCount(groups.length)} distinct ${plural(groups.length, "exception")}` +
    ` · ${formatCount(occurrences)} ${plural(occurrences, "occurrence")}` +
    ` in the last ${windowLabel}`;

  const fresh = countNewInWindow(groups);
  return fresh === 0
    ? `${opening}. None of them started here.`
    : `${opening}, ${fresh} of which started inside it.`;
}

/**
 * Said wherever the deploy below is shown, and not optional.
 *
 * The link is an ordering and nothing else. A reader who takes "the deploy before
 * this" for "the deploy that did this" reverts a release that was never the
 * problem, so the sentence that prevents it travels with the fact.
 */
export const DEPLOY_IS_NOT_A_CAUSE =
  "The last deploy to reach this group's services before it appeared. That it came " +
  "first is not evidence that it caused this.";

/**
 * "4 minutes after deploy 2026.09.06-1 on demo-payments", or nothing.
 *
 * Nothing when the API recorded no deploy before this group — which is the ordinary
 * case, because most projects never post a deploy notification, and an absent
 * reading is left absent rather than filled in with a sentence about not knowing.
 *
 * The wording is deliberate and is the same everywhere it appears: it states the
 * order, and `DEPLOY_IS_NOT_A_CAUSE` states what the order is not.
 */
export function describePrecedingDeploy(group: ErrorGroupSummary): string | undefined {
  const deploy = group.preceding_deploy;
  if (!deploy) return undefined;

  const gap = formatGap(deploy.deployed_at, group.first_seen);
  const shipped = `deploy ${deploy.version} on ${deploy.service_name}`;
  return gap === undefined ? `After ${shipped}` : `${gap} after ${shipped}`;
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
