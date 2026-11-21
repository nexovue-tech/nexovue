import type { SortDirection } from "@/shared/components/DataTable";
import { savedUnder } from "@/shared/dashboards-for-service";

import type { DashboardSummary, DashboardTemplate } from "./api";

/**
 * How the dashboard list is ordered, filtered and marked (F20, FR36, AC45, AC46, AC81).
 * Pure, so it is tested as data: the screen only draws what these return.
 */

export type ListColumn = "title" | "owner" | "panels" | "opened" | "changed" | "not_reading";

export interface ListOrder {
  column: ListColumn;
  direction: SortDirection;
}

export const DEFAULT_ORDER: ListOrder = { column: "title", direction: "ascending" };

/** A dashboard untouched this long is marked (FR36, a *guess*). */
export const STALE_AFTER_DAYS = 30;
const MS_PER_DAY = 86_400_000;

/** What a row without an owner says, and so what an owner sort orders it as (FR38). */
export const FORMER_MEMBER = "Former member";

/** Asking for the sorted column again reverses it; a new column starts ascending. */
export function nextOrder(current: ListOrder, asked: ListColumn): ListOrder {
  if (current.column !== asked) return { column: asked, direction: "ascending" };
  return {
    column: asked,
    direction: current.direction === "ascending" ? "descending" : "ascending",
  };
}

/**
 * The rows in order. Ascending last opened is "least opened first", so a dashboard nobody
 * ever opened comes first (AC46); descending puts it last. Ties fall to the title, then the
 * id, so the order is the same on every draw.
 */
export function sortRows(rows: readonly DashboardSummary[], order: ListOrder): DashboardSummary[] {
  const sign = order.direction === "ascending" ? 1 : -1;
  return [...rows].sort(
    (a, b) => sign * compareBy(order.column, a, b) || compareTitles(a, b) || compareIds(a, b),
  );
}

function compareBy(column: ListColumn, a: DashboardSummary, b: DashboardSummary): number {
  switch (column) {
    case "title":
      return compareTitles(a, b);
    case "owner":
      return (a.owner ?? FORMER_MEMBER).localeCompare(b.owner ?? FORMER_MEMBER, undefined, {
        sensitivity: "base",
      });
    case "panels":
      return a.panel_count - b.panel_count;
    case "opened":
      return instant(a.last_viewed_at) - instant(b.last_viewed_at);
    case "changed":
      return instant(a.updated_at) - instant(b.updated_at);
    case "not_reading":
      return a.panels_not_reading - b.panels_not_reading;
  }
}

/** Never opened is before every time there is. */
function instant(value: string | null): number {
  return value === null ? Number.NEGATIVE_INFINITY : Date.parse(value);
}

const compareTitles = (a: DashboardSummary, b: DashboardSummary): number =>
  a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
const compareIds = (a: DashboardSummary, b: DashboardSummary): number => a.id.localeCompare(b.id);

/** Rows whose title holds the typed text, ignoring case and the spaces around it (FR36). */
export function filterByTitle(
  rows: readonly DashboardSummary[],
  typed: string,
): DashboardSummary[] {
  const wanted = typed.trim().toLowerCase();
  if (wanted === "") return [...rows];
  return rows.filter((row) => row.title.toLowerCase().includes(wanted));
}

/**
 * Not opened for 30 days (FR36, AC45): counted from the last time a batch read ran, or from
 * creation for one nobody has opened, so a dashboard made and forgotten is marked too.
 */
export function isStale(row: DashboardSummary, now: number): boolean {
  const since = Date.parse(row.last_viewed_at ?? row.created_at);
  return now - since > STALE_AFTER_DAYS * MS_PER_DAY;
}

/** "3 panels not reading" where the last stamped read found some, nothing otherwise. */
export function notReadingWords(count: number): string | undefined {
  if (count <= 0) return undefined;
  return `${count} ${count === 1 ? "panel" : "panels"} not reading`;
}

/** One of FR39's starting points: open the one saved from it, or start a draft from it. */
export type StartingPoint =
  | { kind: "open"; templateId: string; dashboardId: string; label: string }
  | { kind: "start"; templateId: string; label: string };

/**
 * The templates in the API's order (FR39's), each offered as "Open" once a dashboard holds
 * its title, so the list never invites a second copy (FR39, D20).
 */
export function startingPoints(
  templates: readonly DashboardTemplate[],
  rows: readonly Pick<DashboardSummary, "id" | "title">[],
): StartingPoint[] {
  return templates.map((template) => {
    const saved = savedUnder(template.document.title, rows);
    return saved
      ? {
          kind: "open",
          templateId: template.id,
          dashboardId: saved.id,
          label: `Open ${saved.title}`,
        }
      : { kind: "start", templateId: template.id, label: template.name };
  });
}
