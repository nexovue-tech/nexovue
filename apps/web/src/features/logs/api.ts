import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

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

export const logSearchQueryKey = (
  projectId: string,
  timeWindow: TimeWindow,
  filters: LogSearchFilters,
) =>
  [
    "logs",
    projectId,
    timeWindow.from,
    timeWindow.to,
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
  return useQuery({
    ...liveOptions(timeWindow),
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
