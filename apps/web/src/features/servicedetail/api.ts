import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";
import type { ServiceEdge } from "@/shared/service-peers";

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
  /**
   * All three, because the duration panel draws the distribution's shape.
   * A p99 pulling away from a p95 while the p50 holds is a tail problem; a p95
   * alone cannot tell that from everything getting slower together.
   */
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
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

export const serviceQueryKey = (projectId: string, serviceName: string, timeWindow: TimeWindow) =>
  ["service-detail", projectId, serviceName, windowKeyOf(timeWindow)] as const;

export const serviceTrendQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-trend", projectId, serviceName, windowKeyOf(timeWindow)] as const;

export const serviceEndpointsQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-endpoints", projectId, serviceName, windowKeyOf(timeWindow)] as const;

export const serviceDependenciesQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["service-dependencies", projectId, windowKeyOf(timeWindow)] as const;

export const serviceErrorsQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-errors", projectId, serviceName, windowKeyOf(timeWindow)] as const;

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
    ...liveOptions(timeWindow),
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
    ...liveOptions(timeWindow),
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
    ...liveOptions(timeWindow),
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
    ...liveOptions(timeWindow),
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
    ...liveOptions(timeWindow),
    queryKey: serviceDependenciesQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceMapResponse>("service-map", {
        searchParams: windowParams(projectId, timeWindow),
        signal,
      }),
  });
}

/**
 * The deploys recorded on this project.
 *
 * Served since the webhook was built and drawn nowhere until now. Read for the
 * project rather than the service — the endpoint has no service filter — and
 * narrowed to the chart's own service by `deployMarkers`.
 */
export interface DeployEventSummary {
  id: string;
  version: string;
  service_name: string;
  deployed_at: string;
  source: string;
}

interface DeployEventListResponse {
  deploys: DeployEventSummary[];
}

export const deploysQueryKey = (projectId: string) => ["deploys", projectId] as const;

export function useDeploys(projectId: string | undefined) {
  return useQuery({
    queryKey: deploysQueryKey(projectId ?? ""),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<DeployEventListResponse>("deploys", {
        searchParams: { project_id: projectId },
        signal,
      }),
    select: (response) => response.deploys,
  });
}

/**
 * One service's latency distribution over time, when it sends an OTLP histogram.
 *
 * **The panel is conditional (OD7), and `available` is how the screen knows.** Most
 * customers will not have one: `latency_quantiles` keeps three quantiles and cannot
 * reconstruct a distribution, and `spans.duration_ms` is post-sampling. The three
 * percentile lines carry the common case; this is the extra a histogram buys.
 */
export interface DurationHeatmapColumn {
  bucket: string;
  counts: number[];
  total: number;
}

export interface DurationHeatmapResponse {
  service_name: string;
  available: boolean;
  metric_name: string;
  unit: string;
  bounds: number[];
  bucket_seconds: number;
  window_start: string;
  window_end: string;
  columns: DurationHeatmapColumn[];
}

export const durationHeatmapQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-duration-heatmap", projectId, serviceName, windowKeyOf(timeWindow)] as const;

export function useDurationHeatmap(
  projectId: string | undefined,
  serviceName: string,
  timeWindow: TimeWindow,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: durationHeatmapQueryKey(projectId ?? "", serviceName, timeWindow),
    enabled: projectId !== undefined && serviceName !== "",
    queryFn: ({ signal }) =>
      apiClient.get<DurationHeatmapResponse>("metrics/duration-heatmap", {
        searchParams: {
          project_id: projectId,
          service_name: serviceName,
          from: timeWindow.from,
          to: timeWindow.to,
        },
        signal,
      }),
  });
}
