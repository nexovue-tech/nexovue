import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import type { TimeWindow } from "@/shared/selection";

/** What real browsers measured. Field names are the API's, unchanged. */

export interface WebVitalSummary {
  /** cls | fcp | inp | lcp | ttfb, lower case, as the SDK spells it. */
  name: string;
  /** Milliseconds for fcp, inp, lcp and ttfb; a unitless ratio for cls. */
  p75: number;
  /** How many readings the percentile was computed from. */
  readings: number;
}

interface WebVitalsResponse {
  /** Only the vitals with a reading in the window. A vital is absent, not zero. */
  vitals: WebVitalSummary[];
}

export const webVitalsQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["web-vitals", projectId, timeWindow.from, timeWindow.to] as const;

/**
 * Each Core Web Vital's p75 over the window.
 *
 * The percentile is computed by the store from a merged quantile state, never here:
 * a p75 cannot be averaged out of smaller p75s, so the screen asks for the answer
 * rather than the parts.
 */
export function useWebVitals(projectId: string | undefined, timeWindow: TimeWindow) {
  return useQuery({
    queryKey: webVitalsQueryKey(projectId ?? "", timeWindow),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<WebVitalsResponse>("rum/web-vitals", {
        searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
        signal,
      }),
  });
}
