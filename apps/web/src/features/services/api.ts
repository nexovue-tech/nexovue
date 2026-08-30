import { useQueries, useQuery } from "@tanstack/react-query";

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
