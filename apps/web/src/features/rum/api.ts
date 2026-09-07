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

/** What a breakdown splits the readings by. The API's own two words. */
export type BreakdownDimension = "page" | "device";

export interface WebVitalGroup {
  /** The page, or the device class. Empty when the browser reported none. */
  key: string;
  /**
   * Whether `key` is the customer's own route template rather than a URL a browser
   * was on. A template is "this page"; a path is "this one URL". Always false for
   * a device breakdown.
   */
  is_route_template: boolean;
  vitals: WebVitalSummary[];
}

export interface WebVitalBreakdown {
  dimension: BreakdownDimension;
  /** Busiest first. */
  groups: WebVitalGroup[];
  /** More groups exist than were returned. */
  truncated: boolean;
}

export const webVitalsQueryKey = (projectId: string, timeWindow: TimeWindow) =>
  ["web-vitals", projectId, timeWindow.from, timeWindow.to] as const;

export const webVitalsBreakdownQueryKey = (
  projectId: string,
  timeWindow: TimeWindow,
  dimension: BreakdownDimension,
) => ["web-vitals-breakdown", dimension, projectId, timeWindow.from, timeWindow.to] as const;

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

/**
 * The same p75s split by page or by device class, busiest group first.
 *
 * A separate read rather than a field on the summary, because the split is the
 * expensive question and a screen showing one page's site-wide numbers should not
 * pay for it. The store does the grouping and the merging: a page's p75 cannot be
 * recovered from the site's.
 */
export function useWebVitalsBreakdown(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  dimension: BreakdownDimension,
) {
  return useQuery({
    queryKey: webVitalsBreakdownQueryKey(projectId ?? "", timeWindow, dimension),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<WebVitalBreakdown>("rum/web-vitals/breakdown", {
        searchParams: {
          project_id: projectId,
          dimension,
          from: timeWindow.from,
          to: timeWindow.to,
        },
        signal,
      }),
  });
}
