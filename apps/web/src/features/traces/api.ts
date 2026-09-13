import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

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
  /**
   * Every service and every (service, operation) the trace touched, not only
   * the root it is named after. These are the same columns the list already
   * filters on, so a reader can go from seeing a service on a row to seeing
   * every trace that touched it.
   */
  span_services: string[];
  span_endpoints: TraceEndpoint[];
}

/** One (service, operation) a trace touched. `GET /` means nothing alone. */
export interface TraceEndpoint {
  service_name: string;
  operation: string;
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
  /**
   * What the span threw. Empty on a span that did not throw — including one
   * that failed: a gRPC `UNAVAILABLE` is an outcome, not an exception, and the
   * API deliberately does not fill these in from a status.
   */
  exception_type?: string;
  exception_message?: string;
  /** The build this span ran on, from `service.version`. Empty when unsaid. */
  deployment_version?: string;
  /** What happened inside the span, in time order. Usually empty. */
  events?: SpanEvent[];
  /** Spans in *other* traces this one points at — a queue hop, usually. */
  links?: SpanLink[];
  attributes: Record<string, unknown>;
}

export interface SpanEvent {
  at: string;
  name: string;
  attributes: Record<string, unknown>;
}

export interface SpanLink {
  trace_id: string;
  span_id: string;
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
) => ["traces", projectId, windowKeyOf(timeWindow), toSearchParams(filters)] as const;

export const traceFacetsQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["trace-facets", projectId, windowKeyOf(timeWindow)] as const;

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
    ...liveOptions(timeWindow),
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

/**
 * The logs emitted under one trace.
 *
 * Declared here rather than imported from `features/logs`: a feature never
 * imports another feature, and what is shared is the API's contract, not that
 * screen's code.
 */
export interface TraceLogLine {
  observed_at: string;
  service_name: string;
  severity: string;
  severity_number: number;
  body: string;
  trace_id: string;
  span_id: string;
  attributes: Record<string, unknown>;
}

interface LogsForTraceResponse {
  /**
   * Whether the store holds the trace these logs point at. False is a fact, not
   * a failure: logs are written in full and traces are tail-sampled, so a
   * healthy trace is routinely judged and dropped while its log lines survive.
   */
  trace: { trace_id: string; exists: boolean };
  logs: TraceLogLine[];
}

export const traceLogsQueryKey = (projectId: string, traceId: string) =>
  ["trace-logs", projectId, traceId] as const;

/**
 * Read for the trace, not for the window.
 *
 * The endpoint bounds itself on `span_trace_lookup`, so no time range is sent:
 * a trace found from a week-old link should show the lines it was emitted with,
 * not the lines inside whatever range the picker happens to be on.
 */
export function useTraceLogs(projectId: string | undefined, traceId: string) {
  return useQuery({
    queryKey: traceLogsQueryKey(projectId ?? "", traceId),
    enabled: projectId !== undefined && traceId !== "",
    queryFn: ({ signal }) =>
      apiClient.get<LogsForTraceResponse>("logs", {
        searchParams: { project_id: projectId, trace_id: traceId },
        signal,
      }),
  });
}
