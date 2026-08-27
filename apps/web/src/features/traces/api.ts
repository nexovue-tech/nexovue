import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/** The trace half of the M1 contract. Field names are the API's, unchanged. */

/** The trace summary's status is mapped server-side, so its values are known. */
export type TraceStatusCode = "STATUS_CODE_UNSET" | "STATUS_CODE_OK" | "STATUS_CODE_ERROR";

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

const PAGE_SIZE = 100;

export const traceListQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["traces", projectId, timeWindow.from, timeWindow.to] as const;

export const traceQueryKey = (projectId: string, traceId: string) =>
  ["trace", projectId, traceId] as const;

/**
 * Newest first, one page at a time. The cursor is opaque — the only thing the browser
 * may do with it is hand it back.
 */
export function useTraceList(projectId: string | undefined, timeWindow: TimeWindow) {
  return useInfiniteQuery({
    queryKey: traceListQueryKey(projectId ?? "", timeWindow),
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
