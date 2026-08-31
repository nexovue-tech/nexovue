import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/**
 * What one service's screen reads. Five endpoints, and field names are the API's,
 * unchanged.
 *
 * Four of the five are scoped to this service by the server, by a bound `service_name`
 * that narrows a read this tenant is already entitled to. The fifth is the dependency
 * graph, which has no per-service form and is sliced here.
 */

/** The numbers a service row and an endpoint row are both read for. */
export interface HealthMetrics {
  request_count: number;
  error_count: number;
  /** Per minute of exactly this window, so two window lengths compare directly. */
  requests_per_minute: number;
  /** A fraction, not a percentage: 0.05 is one call in twenty. */
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

export interface ServiceHealth extends HealthMetrics {
  service_name: string;
}

export interface EndpointHealth extends HealthMetrics {
  service_name: string;
  /** SERVER or CONSUMER — an HTTP route and a queue handler can share a name. */
  span_kind: string;
  endpoint: string;
}

/** One bucket of this service's history. Same three numbers the aggregate carries. */
export interface TrendPoint {
  bucket: string;
  requests_per_minute: number;
  error_rate: number;
  p95_ms: number;
}

interface ServiceTrend {
  service_name: string;
  points: TrendPoint[];
}

interface ServiceTrendResponse {
  window_start: string;
  window_end: string;
  /** The server picks it from the window length; the axis is drawn against it. */
  bucket_seconds: number;
  services: ServiceTrend[];
}

interface ServiceHealthResponse {
  window_start: string;
  window_end: string;
  /**
   * Null when nothing by that name reported in the window — and that is not a 404.
   * The rollups know which services reported in *this* window, not which exist, so
   * "no row" means "silent here" and the screen has to say that rather than "missing".
   */
  service: ServiceHealth | null;
}

interface EndpointListResponse {
  window_start: string;
  window_end: string;
  endpoints: EndpointHealth[];
}

/**
 * A queue hop, as the API spells it. It can be retried for hours; a direct call fails
 * the caller now, so the two are never read as the same dependency.
 */
export const EDGE_QUEUE = "queue";

export interface ServiceEdge {
  caller: string;
  callee: string;
  /** `call` or `queue`; anything else is read as a call. */
  edge_kind: string;
  call_count: number;
  error_count: number;
  calls_per_minute: number;
  error_rate: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

interface ServiceMapResponse {
  window_start: string;
  window_end: string;
  services: string[];
  edges: ServiceEdge[];
}

/** One exception, however many times it happened in this service. */
export interface ErrorGroupSummary {
  group_id: string;
  exception_type: string;
  /** Ids, numbers, hosts and addresses collapsed to tokens. This is what the group is. */
  message_template: string;
  /** What one instance actually said, tokens and all. */
  latest_message: string;
  /**
   * De-duplicated, and counted for this service only: the same group elsewhere in the
   * project is not in this number.
   */
  occurrences: number;
  first_seen: string;
  last_seen: string;
}

interface ErrorGroupListResponse {
  groups: ErrorGroupSummary[];
}

/**
 * A service we can only see from the outside — it has no screen of its own.
 *
 * The test is the parentheses, not a fixed list. A callee that never reported for
 * itself is named from what the caller's own span said about its peer —
 * `(valkey-cart)`, `(postgresql)`, `(kafka)` — so the set of these names is open, and
 * a real `service.name` is a resource attribute that cannot contain parentheses.
 */
export function isPlaceholderService(name: string): boolean {
  return name.startsWith("(") && name.endsWith(")");
}

export const serviceQueryKey = (projectId: string, serviceName: string, timeWindow: TimeWindow) =>
  ["service-detail", projectId, serviceName, timeWindow.from, timeWindow.to] as const;

export const serviceTrendQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-trend", projectId, serviceName, timeWindow.from, timeWindow.to] as const;

export const serviceEndpointsQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-endpoints", projectId, serviceName, timeWindow.from, timeWindow.to] as const;

export const serviceDependenciesQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["service-dependencies", projectId, timeWindow.from, timeWindow.to] as const;

export const serviceErrorsQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-errors", projectId, serviceName, timeWindow.from, timeWindow.to] as const;

function windowParams(projectId: string | undefined, timeWindow: TimeWindow) {
  return { project_id: projectId, from: timeWindow.from, to: timeWindow.to };
}

/**
 * This service's aggregate over the window — the six numbers the header is built on.
 *
 * One named row, asked for by name. The name is a path segment, so it is escaped.
 */
export function useService(
  projectId: string | undefined,
  serviceName: string,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: serviceQueryKey(projectId ?? "", serviceName, timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceHealthResponse>(`services/${encodeURIComponent(serviceName)}`, {
        searchParams: windowParams(projectId, timeWindow),
        signal,
      }),
    // The wrapper carries the window the screen already knows; the row is the answer.
    select: (response: ServiceHealthResponse) => response.service ?? undefined,
  });
}

/**
 * This service's history, bucket by bucket.
 *
 * The name goes on the wire as `service_name`, a bound query parameter. It narrows a
 * read the tenant is already entitled to; it never widens one.
 */
export function useServiceTrend(
  projectId: string | undefined,
  serviceName: string,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: serviceTrendQueryKey(projectId ?? "", serviceName, timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceTrendResponse>("services/trend", {
        searchParams: { ...windowParams(projectId, timeWindow), service_name: serviceName },
        signal,
      }),
    // One name in, at most one series back. The screen wants the points, not the wrapper.
    select: (response: ServiceTrendResponse) => ({
      bucketSeconds: response.bucket_seconds,
      points: response.services[0]?.points ?? [],
    }),
  });
}

/** This service's endpoints, worst first. The name is a path segment, so it is escaped. */
export function useServiceEndpoints(
  projectId: string | undefined,
  serviceName: string,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: serviceEndpointsQueryKey(projectId ?? "", serviceName, timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<EndpointListResponse>(`services/${encodeURIComponent(serviceName)}/endpoints`, {
        searchParams: windowParams(projectId, timeWindow),
        signal,
      }),
  });
}

/**
 * The exceptions thrown in this service, most frequent first.
 *
 * Narrowed by the server on a bound `service_name`. Filtering the project-wide list
 * here instead would silently under-report: that list is capped, so a quiet service's
 * groups can fall off the end of it and nothing on this screen could say they had.
 *
 * Its failure is deliberately not the screen's failure, for the same reason the
 * dependency panel's is not — losing one panel must not blank the numbers a person
 * came for.
 */
export function useServiceErrors(
  projectId: string | undefined,
  serviceName: string,
  timeWindow: TimeWindow,
) {
  return useQuery({
    queryKey: serviceErrorsQueryKey(projectId ?? "", serviceName, timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ErrorGroupListResponse>("error-groups", {
        searchParams: { ...windowParams(projectId, timeWindow), service_name: serviceName },
        signal,
      }),
  });
}

/**
 * The window's whole dependency graph, which this screen reads two slices of.
 *
 * There is no per-service edge endpoint, so the map is fetched and filtered here.
 * Its failure is deliberately not the screen's failure: callers and callees are one
 * panel, and losing them must not blank the numbers a person came for.
 */
export function useServiceDependencies(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: serviceDependenciesQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceMapResponse>("service-map", {
        searchParams: windowParams(projectId, timeWindow),
        signal,
      }),
  });
}
