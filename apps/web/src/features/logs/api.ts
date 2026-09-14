import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { API_BASE_PATH, apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";
import { windowKeyOf } from "@/shared/selection";

import type { LogLine } from "./log-feed";

/** The stored half of the screen. The live half is `tail-client.ts`. */

export interface LogSearchFilters {
  /** Case-insensitive substring of the body. Empty means no text filter. */
  query: string;
  serviceName?: string;
  minSeverity?: number;
}

export const NO_FILTERS: LogSearchFilters = { query: "" };

interface LogSearchResponse {
  logs: LogLine[];
  next_cursor: string | null;
}

export interface ServiceFacet {
  service_name: string;
  records: number;
}

export interface SeverityFacet {
  severity: string;
  severity_number: number;
  records: number;
}

interface LogFacetsResponse {
  services: ServiceFacet[];
  severities: SeverityFacet[];
}

const PAGE_SIZE = 200;

/** Undefined is dropped by the client; an empty search box must not become `q=`. */
function searchParams(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  filters: LogSearchFilters,
) {
  return {
    project_id: projectId,
    from: timeWindow.from,
    to: timeWindow.to,
    q: filters.query.trim() || undefined,
    service: filters.serviceName,
    min_severity: filters.minSeverity,
  };
}

/**
 * **`windowKeyOf`, not `from` and `to`.** This was the one window-scoped key in
 * the app built from the window's ends, and the ends move: `useLiveWindow`
 * advances them every ten seconds. A changed key is a query TanStack has never
 * run, so `isPending` went true on every tick and the screen drew its fourteen-row
 * skeleton over lines that were already on it — the exact defect T48 named as
 * "every refresh is a cold load", surviving on the one screen that also has a
 * socket, where it was easy to read as the tail doing something.
 *
 * Pausing did not stop it. The pause on this screen holds the *tail*; the window
 * kept advancing underneath, so the skeleton kept arriving over a feed that was
 * deliberately frozen.
 */
export const logSearchQueryKey = (
  projectId: string,
  timeWindow: TimeWindow,
  filters: LogSearchFilters,
) =>
  [
    "logs",
    projectId,
    windowKeyOf(timeWindow),
    filters.query.trim(),
    filters.serviceName ?? "",
    filters.minSeverity ?? 0,
  ] as const;

export const logFacetsQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["log-facets", projectId, windowKeyOf(timeWindow)] as const;

/** Newest first, one page at a time. The cursor is opaque; we only hand it back. */
export function useLogSearch(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  filters: LogSearchFilters,
) {
  return useInfiniteQuery({
    queryKey: logSearchQueryKey(projectId ?? "", timeWindow, filters),
    enabled: projectId !== undefined,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiClient.get<LogSearchResponse>("logs/search", {
        searchParams: {
          ...searchParams(projectId, timeWindow, filters),
          limit: PAGE_SIZE,
          cursor: pageParam,
        },
        signal,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    select: (data) => ({ logs: data.pages.flatMap((page) => page.logs) }),
  });
}

/**
 * The filter options this window actually holds.
 *
 * Read from the logs rather than from the service list: a service that sends traces but
 * no logs would be offered as a filter that can only ever return nothing.
 */
export function useLogFacets(projectId: string | undefined, timeWindow: TimeWindow) {
  // **Not polled.** The tail is what makes this screen live; these are the filter
  // options the window holds, and they are re-read when the window, the project or
  // the filters change. Polling them was the only thing the interval picker did
  // here, which is why the picker read as a control over the tail and was not one.
  return useQuery({
    queryKey: logFacetsQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<LogFacetsResponse>("logs/facets", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
        },
        signal,
      }),
  });
}

/**
 * The same records as a file, carrying the same question.
 *
 * **A link, never a blob this page builds** — the reasoning `traces/api.ts` records:
 * the browser saves a same-origin response that says `attachment`, and every filter
 * goes on the URL so the link is the same question a reader can paste to a colleague.
 *
 * **This reads storage, which runs behind the tail.** `log-export.ts` is the other
 * half of the answer, and the control offers both because neither is complete alone.
 */
export function logExportUrl(
  projectId: string,
  timeWindow: TimeWindow,
  filters: LogSearchFilters,
): string {
  const url = new URL(`${API_BASE_PATH}logs/export.csv`, window.location.origin);
  url.searchParams.set("project_id", projectId);
  url.searchParams.set("from", timeWindow.from);
  url.searchParams.set("to", timeWindow.to);
  const query = filters.query.trim();
  if (query) url.searchParams.set("q", query);
  if (filters.serviceName) url.searchParams.set("service", filters.serviceName);
  if (filters.minSeverity) url.searchParams.set("min_severity", String(filters.minSeverity));
  return url.toString();
}
