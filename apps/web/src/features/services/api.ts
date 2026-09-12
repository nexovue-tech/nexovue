import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/** The service-overview half of the M6 contract. Field names are the API's, unchanged. */

/** What a service and an endpoint have in common: the numbers a row is read for. */
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

/** One bucket of a service's history. The same numbers the row carries. */
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
  /** The server picks it from the window length; a client that assumed a minute would mis-scale. */
  bucket_seconds: number;
  services: ServiceTrend[];
}

/** What one expanded service knows about its endpoints. */
export interface EndpointGroup {
  status: "pending" | "error" | "success";
  endpoints: readonly EndpointHealth[];
  refetch: () => void;
}

export const serviceListQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["services", projectId, timeWindow.from, timeWindow.to] as const;

export const serviceEndpointsQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
) => ["service-endpoints", projectId, serviceName, timeWindow.from, timeWindow.to] as const;

/**
 * Every service that reported in the window. The API returns them already ranked —
 * errors, then the slow tail, then volume — and that order is the product, so nothing
 * here re-sorts it.
 */
export function useServiceList(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: serviceListQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceListResponse>("services", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

export const serviceTrendQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["service-trend", projectId, timeWindow.from, timeWindow.to] as const;

/**
 * Every service's history, in one request rather than one per row.
 *
 * The endpoint returns all of them together and the server chooses the bucket, so a
 * fifty-row list costs one read. Keyed by name because that is how a row looks its own
 * series up; a service missing from the map has no trend to draw and renders the em dash
 * rather than disappearing.
 *
 * Deliberately a separate query from the list: the rows must appear as soon as the list
 * lands, and fill in their trends when this arrives. A screen that waited for both would
 * be slower than the one it replaces.
 */
export function useServiceTrend(
  projectId: string | undefined,
  timeWindow: TimeWindow,
): ReadonlyMap<string, readonly TrendPoint[]> {
  const { data } = useQuery({
    queryKey: serviceTrendQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceTrendResponse>("services/trend", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });

  return useMemo(
    () => new Map((data?.services ?? []).map((trend) => [trend.service_name, trend.points])),
    [data],
  );
}

/**
 * The endpoints of the services that are open, one request each.
 *
 * Per service rather than one project-wide read: the project read is capped, so on a
 * large route table it would return a prefix and leave later services looking empty —
 * which is indistinguishable from silent, and wrong.
 */
export function useEndpointsByService(
  projectId: string | undefined,
  serviceNames: readonly string[],
  timeWindow: TimeWindow,
): ReadonlyMap<string, EndpointGroup> {
  return useQueries({
    queries: serviceNames.map((serviceName) => ({
      queryKey: serviceEndpointsQueryKey(projectId ?? "", serviceName, timeWindow),
      enabled: projectId !== undefined,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        apiClient.get<EndpointListResponse>(
          `services/${encodeURIComponent(serviceName)}/endpoints`,
          {
            searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
            signal,
          },
        ),
    })),
    combine: (results) =>
      new Map(
        results.map((result, index) => [
          serviceNames[index],
          {
            status: result.isPending ? "pending" : result.isError ? "error" : "success",
            endpoints: result.data?.endpoints ?? [],
            refetch: () => {
              void result.refetch();
            },
          } satisfies EndpointGroup,
        ]),
      ),
  });
}
