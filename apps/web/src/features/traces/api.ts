import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

import type { TraceFilters } from "./filters";
import { toSearchParams } from "./filters";

/** The trace half of the M1 contract. Field names are the API's, unchanged. */

/** The short OTel form, which is what storage holds and what the API returns. */
export type TraceStatusCode = "UNSET" | "OK" | "ERROR";

/**
 * A span's status is passed through from storage verbatim, and storage holds the short
 * OTel form (`ERROR`) rather than the protobuf enum name (`STATUS_CODE_ERROR`). Neither
 * form can be ruled out, so the prefix is stripped before comparing.
 */
const STATUS_ENUM_PREFIX = /^STATUS_CODE_/;

export function normalizeStatus(statusCode: string): string {
  return statusCode.replace(STATUS_ENUM_PREFIX, "");
}

export function spanFailed(span: Span): boolean {
  return normalizeStatus(span.status_code) === "ERROR";
}

/**
 * A trace failed if anything in it did. `error_count` is the reliable half — the
 * status is compared through `normalizeStatus` because comparing the raw string
 * against one spelling is exactly how this stopped working before.
 */
export function traceFailed(trace: TraceSummary): boolean {
  return trace.error_count > 0 || normalizeStatus(trace.status_code) === "ERROR";
}

/** Non-HTTP spans carry 0 rather than omitting the field; 0 is not a status. */
export function httpStatusOf(span: Span): number | undefined {
  return span.http_status ? span.http_status : undefined;
}

export interface TraceSummary {
  trace_id: string;
  root_service: string;
  root_name: string;
  start_time: string;
  duration_ms: number;
  span_count: number;
  error_count: number;
  status_code: TraceStatusCode;
}

export interface Span {
  span_id: string;
  /** Empty string for a root span, never null. */
  parent_span_id: string;
  service_name: string;
  span_name: string;
  span_kind: string;
  start_time: string;
  duration_ms: number;
  status_code: string;
  status_message: string;
  http_method?: string;
  http_route?: string;
  http_status?: number;
  /**
   * A gRPC span carries none of the three above. Its status is the RPC status
   * name — "OK", "UNAVAILABLE" — never an HTTP code, which is why it is a
   * separate field rather than a second meaning for `http_status`.
   */
  rpc_system?: string;
  rpc_method?: string;
  rpc_status?: string;
  attributes: Record<string, unknown>;
}

export interface TraceDetail {
  trace_id: string;
  start_time: string;
  duration_ms: number;
  span_count: number;
  error_count: number;
  spans: Span[];
}

interface TraceListResponse {
  traces: TraceSummary[];
  next_cursor: string | null;
}

export interface ServiceFacet {
  service_name: string;
  spans: number;
  traces: number;
}

export interface OperationFacet {
  service_name: string;
  operation: string;
  spans: number;
  traces: number;
}

/** What the window holds, counted with no filters — see `explainEmptyResult`. */
export interface TraceFacetsResponse {
  services: ServiceFacet[];
  operations: OperationFacet[];
}

const PAGE_SIZE = 100;

export const traceListQueryKey = (
  projectId: string,
  timeWindow: TimeWindow,
  filters: TraceFilters,
) => ["traces", projectId, timeWindow.from, timeWindow.to, toSearchParams(filters)] as const;

export const traceFacetsQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["trace-facets", projectId, timeWindow.from, timeWindow.to] as const;

export const traceQueryKey = (projectId: string, traceId: string) =>
  ["trace", projectId, traceId] as const;

/**
 * Newest first, one page at a time. The cursor is opaque — the only thing the browser
 * may do with it is hand it back.
 *
 * The filters are part of the key, so changing one is a different query rather than a
 * refetch of the same one: the previous page's rows never briefly show under the new
 * filter, which would read as the filter not working.
 */
export function useTraceList(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  filters: TraceFilters,
) {
  return useInfiniteQuery({
    queryKey: traceListQueryKey(projectId ?? "", timeWindow, filters),
    enabled: projectId !== undefined,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiClient.get<TraceListResponse>("traces", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          limit: PAGE_SIZE,
          cursor: pageParam,
          ...toSearchParams(filters),
        },
        signal,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    select: (data) => ({
      traces: data.pages.flatMap((page) => page.traces),
      pageCount: data.pages.length,
    }),
  });
}

/**
 * The filter options this window holds. Deliberately unfiltered: it is what lets an
 * empty result say which filter emptied it rather than only that it is empty.
 */
export function useTraceFacets(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: traceFacetsQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<TraceFacetsResponse>("traces/facets", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

export function useTrace(projectId: string | undefined, traceId: string) {
  return useQuery({
    queryKey: traceQueryKey(projectId ?? "", traceId),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<TraceDetail>(`traces/${encodeURIComponent(traceId)}`, {
        searchParams: { project_id: projectId },
        signal,
      }),
  });
}
