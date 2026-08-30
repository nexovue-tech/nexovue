import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/** The service-map half of the M6 contract. Field names are the API's, unchanged. */

/**
 * The two kinds of dependency the pipeline records, spelled as the API spells them.
 * A queue hop can be retried for hours; a direct call fails the caller now, so they
 * are never drawn as the same arrow.
 */
export const EDGE_CALL = "call";
export const EDGE_QUEUE = "queue";

/**
 * Reserved names the pipeline writes rather than inventing a service. A real
 * `service.name` cannot contain parentheses, so none can collide with one — which is
 * also why they are safe to test for by string.
 *
 * `(uninstrumented)` is now the last resort, not the only one. A callee that never
 * reported for itself is named from what the caller's own span said about its peer —
 * `(valkey-cart)`, `(postgresql)`, `(kafka)` — and only a span that said nothing at
 * all lands here. That is why the test below is the parentheses, not this pair: the
 * set of reserved names is open.
 */
export const UNINSTRUMENTED_SERVICE = "(uninstrumented)";
export const UNKNOWN_SERVICE = "(unknown)";

export interface ServiceEdge {
  caller: string;
  callee: string;
  /** `call` or `queue`; anything else is drawn as a call. */
  edge_kind: string;
  /**
   * The technology the caller says it reached this callee over — `redis`,
   * `postgresql`, `kafka`, `grpc`. Empty whenever the callee reported for itself.
   */
  callee_kind: string;
  call_count: number;
  error_count: number;
  calls_per_minute: number;
  /** A fraction, not a percentage: 0.05 is one call in twenty. */
  error_rate: number;
  /**
   * The caller's own span. On a `call` that is the request it waited on, network
   * included; on a `queue` it is the publish, not the delivery lag — the producer
   * and the consumer keep different clocks.
   */
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

export interface ServiceMapResponse {
  window_start: string;
  window_end: string;
  /** Every service named by an edge, sorted. A service with no dependencies is absent. */
  services: string[];
  edges: ServiceEdge[];
}

/** A service we can only see from the outside: something calls it, it sends us nothing. */
export function isPlaceholderService(name: string): boolean {
  return name.startsWith("(") && name.endsWith(")");
}

export const serviceMapQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["service-map", projectId, timeWindow.from, timeWindow.to] as const;

export function useServiceMap(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: serviceMapQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<ServiceMapResponse>("service-map", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}
