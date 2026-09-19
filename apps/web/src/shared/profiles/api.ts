import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api-client";
import type { LiveWindow, TimeWindow } from "../selection";
import { liveOptions, windowKeyOf } from "../selection";

/**
 * What profiling knows about one service or one span (F17). In shared/ because the
 * service page (FR11) and the span view (FR10) both show it, and neither may import
 * the profiles feature.
 */

/** A profile type a service sent: what its values count, and in what unit. */
export interface ProfileType {
  sample_type: string;
  unit: string;
}

export interface ProfiledService {
  service_name: string;
  types: ProfileType[];
  last_received: string;
}

export function useProfiledServices(
  projectId: string | undefined,
  timeWindow: TimeWindow | LiveWindow,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: ["profiles", "services", projectId ?? "", windowKeyOf(timeWindow)] as const,
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<{ services: ProfiledService[] }>("profiles/services", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}

/**
 * Whether a span has samples of its own (`linked`); whether its service was profiled
 * around it but no sample landed in it (`no_samples`); or not profiled at all.
 */
export interface SpanProfile {
  state: "linked" | "no_samples" | "not_profiled";
  linked_samples: number;
  types: string[];
}

export interface SpanRef {
  service: string;
  traceId: string;
  spanId: string;
  /** The span's own start and end, ISO 8601. */
  from: string;
  to: string;
}

export function useSpanProfile(projectId: string | undefined, span: SpanRef | undefined) {
  return useQuery({
    queryKey: ["profiles", "span", projectId ?? "", span] as const,
    enabled: projectId !== undefined && span !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<SpanProfile>("profiles/span", {
        searchParams: {
          project_id: projectId,
          service_name: span?.service,
          trace_id: span?.traceId,
          span_id: span?.spanId,
          from: span?.from,
          to: span?.to,
        },
        signal,
      }),
  });
}
