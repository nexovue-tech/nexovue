/**
 * Turning the project's buckets into the three panels the dashboard draws.
 *
 * Three charts rather than one, for the reason `servicedetail/trend-series.ts`
 * gives: rate, failure share and latency are three units, and one y axis would
 * either flatten two of them or lie about the third.
 *
 * The difference here is the duration panel. A service's own chart draws p95
 * alone; this one draws p50, p95 and p99 **on one scale**, because the shape of
 * a latency distribution is the three lines together — a p99 pulling away from
 * a p95 while the p50 holds is the reading, and three separately-scaled lines
 * would draw that as three flat lines.
 */

import type { PlotRows, SeriesToken } from "@/shared/components/TrendChart";

import type { OverviewPoint } from "./api";

const MS_PER_SECOND = 1000;
const PERCENT = 100;

export type OverviewMetric = "rate" | "errorRate" | "latency";

export interface OverviewPlot {
  metric: OverviewMetric;
  label: string;
  seriesToken: SeriesToken | "critical";
  /** One row per line. The duration panel has three; the others have one. */
  lines: { label: string; data: PlotRows }[];
  /** Every bucket of every line read zero. */
  flatAtZero: boolean;
  zeroText: string;
}

const seconds = (points: readonly OverviewPoint[]): number[] =>
  points.map((point) => Date.parse(point.bucket) / MS_PER_SECOND);

export function buildOverviewPlots(points: readonly OverviewPoint[]): OverviewPlot[] {
  const x = seconds(points);
  const rate = points.map((point) => point.requests_per_minute);
  // A share, drawn as a percentage: an axis in fractions makes a reader
  // multiply, and this is the panel they look at first.
  const errors = points.map((point) => point.error_rate * PERCENT);

  return [
    {
      metric: "rate",
      label: "Rate",
      seriesToken: 1,
      lines: [{ label: "Requests/min", data: [x, rate] }],
      flatAtZero: allZero(rate),
      zeroText: "No requests reached any service in this window.",
    },
    {
      // Failure share is the critical tone rather than a palette hue: it is the
      // one panel where a rise is bad, and colour should say so.
      metric: "errorRate",
      label: "Errors",
      seriesToken: "critical",
      lines: [{ label: "Failure share", data: [x, errors] }],
      flatAtZero: allZero(errors),
      zeroText: "Nothing failed in this window.",
    },
    {
      metric: "latency",
      label: "Duration",
      seriesToken: 2,
      lines: [
        { label: "p50", data: [x, points.map((point) => point.p50_ms)] },
        { label: "p95", data: [x, points.map((point) => point.p95_ms)] },
        { label: "p99", data: [x, points.map((point) => point.p99_ms)] },
      ],
      flatAtZero: points.every(
        (point) => point.p50_ms === 0 && point.p95_ms === 0 && point.p99_ms === 0,
      ),
      zeroText: "No request took a measurable time in this window.",
    },
  ];
}

/**
 * A line pinned to the axis is indistinguishable from a broken chart, so the
 * screen says it in words instead of drawing it — the same rule the service's
 * own charts follow.
 */
function allZero(values: readonly number[]): boolean {
  return values.length > 0 && values.every((value) => value === 0);
}

/** The one scale the three duration lines share. */
export function sharedCeiling(plot: OverviewPlot): number {
  let ceiling = 0;
  for (const line of plot.lines) {
    for (const value of line.data[1]) if (value > ceiling) ceiling = value;
  }
  return ceiling;
}
