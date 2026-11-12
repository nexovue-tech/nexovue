import { useQuery } from "@tanstack/react-query";

import { apiClient } from "./api-client";
import { DASHBOARD_RANGES } from "./selection";

/**
 * The dashboards that can show one service, and the window one opens on (F20, FR57,
 * AC82, AC102, AC117). In `shared/` because three feature screens offer it (a finding, a
 * service, an alert incident) and a feature may not import another.
 */

/** A row of the answer: only what a link to the dashboard needs. */
export interface DashboardForService {
  id: string;
  title: string;
  uses_picker: boolean;
}

export interface DashboardsForService {
  dashboards: DashboardForService[];
  /** More than the five listed exist: "See all" (FR57). */
  more: boolean;
}

/**
 * The live dashboard holding a title, ignoring case, as the API's unique index does (D20).
 * A template saved under its own title is found this way: the list's "Open" (FR39) and a
 * service's "Open Service health for checkout" (FR41) rest on it.
 */
export function savedUnder<Row extends { title: string }>(
  title: string,
  rows: readonly Row[],
): Row | undefined {
  const wanted = title.toLowerCase();
  return rows.find((row) => row.title.toLowerCase() === wanted);
}

/** *Service health*'s id in `GET /dashboards/templates` (`dashboards/templates.py`). */
export const SERVICE_HEALTH = "service_health";

interface TemplateSummary {
  id: string;
  name: string;
  document: { title: string };
}

/** The organization's live dashboards, as the list answers: only what finding one needs. */
interface DashboardTitle {
  id: string;
  title: string;
}

/** What a service's screen offers for *Service health* (FR41, AC138). */
export type ServiceHealthOffer =
  | { kind: "pending" }
  | { kind: "failed"; error: unknown; retry: () => void }
  | { kind: "open"; dashboardId: string; title: string }
  | { kind: "start"; templateId: string }
  | { kind: "none" };

/**
 * Whether *Service health* is saved, read from the list and the templates. Under the
 * "for-service" key, so a save anywhere reads it again (`features/dashboards/api.ts`).
 */
export function useServiceHealthOffer(): ServiceHealthOffer {
  const templates = useQuery({
    queryKey: ["dashboards", "for-service", "templates"] as const,
    queryFn: ({ signal }) =>
      apiClient.get<{ templates: TemplateSummary[] }>("dashboards/templates", { signal }),
    // Fixed in the code: nothing a person does changes them.
    staleTime: Number.POSITIVE_INFINITY,
  });
  const dashboards = useQuery({
    queryKey: ["dashboards", "for-service", "titles"] as const,
    queryFn: ({ signal }) =>
      apiClient.get<{ dashboards: DashboardTitle[] }>("dashboards", { signal }),
  });
  if (templates.isError || dashboards.isError) {
    return {
      kind: "failed",
      error: templates.error ?? dashboards.error,
      retry: () => {
        if (templates.isError) void templates.refetch();
        if (dashboards.isError) void dashboards.refetch();
      },
    };
  }
  if (templates.data === undefined || dashboards.data === undefined) return { kind: "pending" };
  return serviceHealthOffer(templates.data.templates, dashboards.data.dashboards);
}

/** Open the one saved, or start one: never a second copy (FR41, AC138). */
export function serviceHealthOffer(
  templates: readonly TemplateSummary[],
  dashboards: readonly DashboardTitle[],
): ServiceHealthOffer {
  const template = templates.find((each) => each.id === SERVICE_HEALTH);
  if (template === undefined) return { kind: "none" };
  const saved = savedUnder(template.document.title, dashboards);
  return saved
    ? { kind: "open", dashboardId: saved.id, title: saved.title }
    : { kind: "start", templateId: template.id };
}

export function useDashboardsForService(service: string) {
  return useQuery({
    queryKey: ["dashboards", "for-service", service] as const,
    queryFn: ({ signal }) =>
      apiClient.get<DashboardsForService>("dashboards", { searchParams: { service }, signal }),
  });
}

/** The window a dashboard opens on: live and refreshing, or fixed times (FR57). */
export type OpenWindow =
  | { kind: "live"; rangeId: string }
  | { kind: "fixed"; from: string; to: string };

/** A lead before a finding began, so its onset is on the chart (a *guess*). */
export const LEAD_MINUTES = 10;
/** A margin on each side of a closed finding (a *guess*): a quarter of its length, at least 10 minutes. */
export const MARGIN_MINUTES = 10;
export const MARGIN_SHARE = 0.25;
const MS_PER_MINUTE = 60_000;
/** The API's longest fixed window is this plus a day (`dashboard-search.ts`); stay inside 30. */
const LONGEST_FIXED_MS = 30 * 24 * 60 * MS_PER_MINUTE;

/**
 * From something still open: the shortest live range that starts before it began, so it
 * refreshes with it. Past 30 days, the longest there is.
 */
export function windowForOpen(startedAt: string, now: number): OpenWindow {
  const needed = now - Date.parse(startedAt) + LEAD_MINUTES * MS_PER_MINUTE;
  const range = DASHBOARD_RANGES.find((each) => each.minutes * MS_PER_MINUTE >= needed);
  return { kind: "live", rangeId: (range ?? DASHBOARD_RANGES[DASHBOARD_RANGES.length - 1]).id };
}

/**
 * From something over: fixed times with a margin on each side, whole minutes, ending no later
 * than `now` and never longer than 30 days (the end is kept, since the latest is the news).
 */
export function windowForClosed(startedAt: string, endedAt: string, now: number): OpenWindow {
  const start = Date.parse(startedAt);
  const end = Date.parse(endedAt);
  const margin = Math.max(MARGIN_MINUTES * MS_PER_MINUTE, (end - start) * MARGIN_SHARE);
  const thisMinute = Math.floor(now / MS_PER_MINUTE) * MS_PER_MINUTE;
  const to = Math.min(Math.ceil((end + margin) / MS_PER_MINUTE) * MS_PER_MINUTE, thisMinute);
  const from = Math.max(
    Math.floor((start - margin) / MS_PER_MINUTE) * MS_PER_MINUTE,
    to - LONGEST_FIXED_MS,
  );
  return { kind: "fixed", from: new Date(from).toISOString(), to: new Date(to).toISOString() };
}
