import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { filterCacheKey } from "@/shared/filters";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";
import type { ServiceEdge } from "@/shared/service-peers";

/** The service-map half of the M6 contract. Field names are the API's, unchanged. */

/**
 * Reserved names the pipeline writes rather than inventing a service. A real
 * `service.name` cannot contain parentheses, so none can collide with one — which is
 * also why they are safe to test for by string.
 *
 * `(uninstrumented)` is now the last resort, not the only one. A callee that never
 * reported for itself is named from what the caller's own span said about its peer —
 * `(valkey-cart)`, `(postgresql)`, `(kafka)` — and only a span that said nothing at
 * all lands here. That is why `isPlaceholderService` in `shared/service-peers.ts`
 * tests the parentheses and not this pair: the set of reserved names is open.
 */
export const UNINSTRUMENTED_SERVICE = "(uninstrumented)";
export const UNKNOWN_SERVICE = "(unknown)";

export interface ServiceMapResponse {
  window_start: string;
  window_end: string;
  /** Every service named by an edge, sorted. A service with no dependencies is absent. */
  services: string[];
  edges: ServiceEdge[];
}

export const serviceMapQueryKey = (projectId: string, timeWindow: TimeWindow, narrowing: string) =>
  ["service-map", projectId, windowKeyOf(timeWindow), narrowing] as const;

export function useServiceMap(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  narrowing: Record<string, string> = {},
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: serviceMapQueryKey(projectId ?? "", timeWindow, filterCacheKey(narrowing)),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceMapResponse>("service-map", {
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
