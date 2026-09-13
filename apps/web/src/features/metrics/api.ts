import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { filterCacheKey } from "@/shared/filters";
import type { TimeWindow } from "@/shared/selection";
import { liveOptions, windowKeyOf } from "@/shared/selection";

/**
 * Every metric a project reports, and the distribution behind a chosen one.
 *
 * The catalogue answers the question no other screen does: *what is in here?* Every
 * other screen reads a metric the product already knows the name of, so a metric an
 * instrumented service sends that nothing happens to chart was invisible — the only way
 * to learn it had arrived was to already know its name.
 */

export interface MetricSummary {
  metric_name: string;
  /**
   * Every shape this name arrived in. More than one is a misconfiguration worth seeing
   * rather than an error worth hiding: nothing stops a service sending `queue.depth` as
   * a gauge and a sum, and picking one would discard the other half silently.
   */
  shapes: string[];
  unit: string;
  /** Distinct attribute sets. */
  series: number;
  /** Distinct (series, instant) pairs — not rows, which are not unique. */
  points: number;
  last_seen: string;
  /** Whether this console can draw every shape this name arrived in. */
  readable: boolean;
}

export interface MetricCatalogueResponse {
  window_start: string;
  window_end: string;
  metrics: MetricSummary[];
  truncated: boolean;
}

/**
 * One bucket layout's distribution.
 *
 * More than one of these on a response means the metric arrived with incompatible
 * bucket boundaries and nothing was merged — see `merged` below.
 */
export interface HistogramLayout {
  /** Upper bounds, ascending. `bucket_counts` has one more entry than this. */
  bounds: number[];
  bucket_counts: number[];
  count: number;
  /** Null when the exporter sent none. Never zero standing in for absent. */
  sum: number | null;
  min: number | null;
  max: number | null;
  /** Who reported this layout — the actionable half of a conflict. */
  services: string[];
  series: number;
  /** **Estimates**, interpolated inside the bucket the rank lands in. */
  p50: number | null;
  p75: number | null;
  p90: number | null;
  p95: number | null;
  p99: number | null;
}

export interface HistogramResponse {
  metric_name: string;
  unit: string;
  description: string;
  window_start: string;
  window_end: string;
  layouts: HistogramLayout[];
  /**
   * False when the name arrived with more than one bucket layout. The screen says so
   * rather than drawing one chart that is the sum of two incompatible ones — which on
   * real data would be adding milliseconds to seconds.
   */
  merged: boolean;
}

export const metricsQueryKey = (projectId: string, timeWindow: TimeWindow, narrowing: string) =>
  ["metrics", "catalogue", projectId, windowKeyOf(timeWindow), narrowing] as const;

export const histogramQueryKey = (projectId: string, metricName: string, timeWindow: TimeWindow) =>
  ["metrics", "histogram", projectId, metricName, windowKeyOf(timeWindow)] as const;

/**
 * The catalogue, narrowed by whatever the URL asks for.
 *
 * `narrowing` reaches the request rather than the rows that come back: the read
 * is cut at five hundred names, and a filter applied after that cut answers
 * about the page. See `filters.ts`.
 */
export function useMetricCatalogue(
  projectId: string | undefined,
  timeWindow: TimeWindow,
  narrowing: Record<string, string>,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: metricsQueryKey(projectId ?? "", timeWindow, filterCacheKey(narrowing)),
    enabled: projectId !== undefined,
    queryFn: ({ signal }) =>
      apiClient.get<MetricCatalogueResponse>("metrics", {
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

/**
 * The distribution behind one metric, read only once a name is chosen.
 *
 * Disabled until then rather than fetched eagerly: the catalogue is the screen, and a
 * histogram nobody selected is a read the telemetry tier cannot spare — an idle console
 * already spends half its allowance re-bucketing the live window.
 */
export function useHistogram(
  projectId: string | undefined,
  metricName: string | undefined,
  timeWindow: TimeWindow,
) {
  return useQuery({
    ...liveOptions(timeWindow),
    queryKey: histogramQueryKey(projectId ?? "", metricName ?? "", timeWindow),
    enabled: projectId !== undefined && metricName !== undefined && metricName !== "",
    queryFn: ({ signal }) =>
      apiClient.get<HistogramResponse>(
        `metrics/${encodeURIComponent(metricName ?? "")}/histogram`,
        {
          searchParams: { project_id: projectId, from: timeWindow.from, to: timeWindow.to },
          signal,
        },
      ),
  });
}
