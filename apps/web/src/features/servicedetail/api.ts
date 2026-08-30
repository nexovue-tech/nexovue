import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/**
 * What one service's screen reads. Four endpoints, all of them already served, and
 * field names are the API's, unchanged.
 *
 * Three of the four are scoped to this service by the server. The ranked list is not:
 * there is no `GET /services/{name}`, so the aggregate row has to be found in the page
 * the overview endpoint returns, and this asks for the largest page the API allows.
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

interface ServiceListResponse {
  window_start: string;
  window_end: string;
  services: ServiceHealth[];
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

/** Reserved names the pipeline writes rather than inventing a service. */
const UNINSTRUMENTED_SERVICE = "(uninstrumented)";
const UNKNOWN_SERVICE = "(unknown)";

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

/** A service we can only see from the outside — it has no screen of its own. */
export function isPlaceholderService(name: string): boolean {
  return name === UNINSTRUMENTED_SERVICE || name === UNKNOWN_SERVICE;
}

/**
 * `MAX_SERVICE_LIMIT` on the API. Asked for in full because this screen needs one
 * named row rather than a readable ranking, and a row outside the page is
 * indistinguishable from a service that was silent.
 */
export const RANKED_PAGE_LIMIT = 500;

export const rankedServicesQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["service-detail-ranked", projectId, timeWindow.from, timeWindow.to] as const;

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

function windowParams(projectId: string | undefined, timeWindow: TimeWindow) {
  return { project_id: projectId, from: timeWindow.from, to: timeWindow.to };
}

/** The ranked page this service's aggregate row is looked up in. */
export function useRankedServices(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: rankedServicesQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceListResponse>("services", {
        searchParams: { ...windowParams(projectId, timeWindow), limit: RANKED_PAGE_LIMIT },
        signal,
      }),
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
