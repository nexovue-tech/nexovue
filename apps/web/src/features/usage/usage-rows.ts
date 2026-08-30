/**
 * The rows of the by-day table.
 *
 * The API returns oldest day first; a usage screen is read from the current day
 * backwards, so the order is reversed here — in one place, and testably.
 */

import type { ProjectDayUsage } from "./api";

export interface UsageRow {
  key: string;
  /** The project's name, or something honest when it no longer has one. */
  projectLabel: string;
  usage: ProjectDayUsage;
}

export const ROW_HEIGHT = 32;

const SHORT_ID_LENGTH = 8;

/**
 * A project that was deleted keeps its usage rows, and it is not in the project list
 * any more. Naming it by its id says what it is without claiming a name it lost.
 */
export function projectLabel(projectId: string, names: ReadonlyMap<string, string>): string {
  return names.get(projectId) ?? `Project ${projectId.slice(0, SHORT_ID_LENGTH)}`;
}

export function buildUsageRows(
  days: readonly ProjectDayUsage[],
  names: ReadonlyMap<string, string>,
): UsageRow[] {
  return days
    .map((usage) => ({
      key: `${usage.day}:${usage.project_id}`,
      projectLabel: projectLabel(usage.project_id, names),
      usage,
    }))
    .sort(byNewestDayThenProject);
}

function byNewestDayThenProject(left: UsageRow, right: UsageRow): number {
  if (left.usage.day !== right.usage.day) return left.usage.day < right.usage.day ? 1 : -1;
  return left.projectLabel.localeCompare(right.projectLabel);
}

/** Days, not rows: two projects unfinished on the same day are one unfinished day. */
export function unsettledDayCount(days: readonly ProjectDayUsage[]): number {
  return new Set(days.filter((day) => !day.settled).map((day) => day.day)).size;
}
