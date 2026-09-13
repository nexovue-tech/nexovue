import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { apiClient } from "@/shared/api-client";
import { filterCacheKey } from "@/shared/filters";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

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

export const serviceListQueryKey = (projectId: string, timeWindow: TimeWindow, narrowing: string) =>
  ["services", projectId, windowKeyOf(timeWindow), narrowing] as const;

export const serviceEndpointsQueryKey = (
  projectId: string,
  serviceName: string,
  timeWindow: TimeWindow,
  narrowing: string,
) => ["service-endpoints", projectId, serviceName, windowKeyOf(timeWindow), narrowing] as const;

/**
 * Every service that reported in the window, ranked and narrowed by the server.
 *
 * The filters and the ordering both travel with the request rather than being
 * applied to the answer. The API returns the first hundred rows of whichever
 * order it was asked for, so anything done to that page here could only
 * rearrange the rows that survived the cut.
 */
export function useServiceList(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  narrowing: Record<string, string>,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: serviceListQueryKey(projectId ?? "", timeWindow, filterCacheKey(narrowing)),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceListResponse>("services", {
        searchParams: {
          project_id: projectId,
          from: timeWindow.from,
          to: timeWindow.to,
          ...narrowing,
        },
        signal,
      }),
  });
}

export const serviceTrendQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["service-trend", projectId, windowKeyOf(timeWindow)] as const;

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
    ...liveOptions(timeWindow),
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
  narrowing: Record<string, string>,
): ReadonlyMap<string, EndpointGroup> {
  const narrowedBy = filterCacheKey(narrowing);
  return useQueries({
    queries: serviceNames.map((serviceName) => ({
      queryKey: serviceEndpointsQueryKey(projectId ?? "", serviceName, timeWindow, narrowedBy),
      enabled: projectId !== undefined,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        apiClient.get<EndpointListResponse>(
          `services/${encodeURIComponent(serviceName)}/endpoints`,
          {
            searchParams: {
              project_id: projectId,
              from: timeWindow.from,
              to: timeWindow.to,
              ...narrowing,
            },
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
