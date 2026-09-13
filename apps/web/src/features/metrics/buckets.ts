import { formatBound } from "@/shared/format/metric-unit";

import type { HistogramLayout } from "./api";

/**
 * Turning a histogram's bounds and counts into something drawable and readable.
 *
 * Pure, and separate from the chart for the usual reason: the arithmetic is what can be
 * wrong in a way nobody sees, and a canvas cannot be asserted on.
 *
 * **The bars are evenly spaced, and that is a deliberate distortion.** Bucket bounds are
 * chosen exponentially — 1, 5, 25, 100, 500 — so a bar chart drawn to a linear value
 * axis would crush every interesting bucket against the left edge and give the widest,
 * emptiest bucket most of the picture. Every histogram viewer worth using draws one bar
 * per bucket instead, and labels the boundaries. What is lost is that bar *width* no
 * longer means anything; what is kept is that bar *height* can be compared at all.
 */

export interface HistogramBar {
  /** What this bucket covers, as a reader would say it: "5ms to 10ms". */
  label: string;
  count: number;
  /** 0 to 1, against the tallest bucket in this layout. */
  height: number;
  /** True for the open-ended bucket above the last bound. */
  unbounded: boolean;
}

/**
 * The bars, tallest-relative, in bucket order.
 *
 * Scaled to the tallest bucket rather than to the total: a distribution where 98% of
 * traffic sits in one bucket is the normal case, and scaling to the total would draw
 * every other bucket as nothing at all.
 */
export function histogramBars(layout: HistogramLayout, unit: string): HistogramBar[] {
  const counts = layout.bucket_counts;
  if (counts.length === 0) return [];

  const tallest = Math.max(...counts);

  return counts.map((count, index) => {
    const upper = layout.bounds[index];
    const lower = index > 0 ? layout.bounds[index - 1] : undefined;
    const unbounded = index >= layout.bounds.length;

    return {
      label: unbounded
        ? `over ${formatBound(layout.bounds.at(-1), unit)}`
        : lower === undefined
          ? `up to ${formatBound(upper, unit)}`
          : `${formatBound(lower, unit)} to ${formatBound(upper, unit)}`,
      count,
      // A zero-height bar and an absent bar look the same; a bucket that holds
      // nothing should read as empty rather than as missing, so the floor is zero
      // and the row below the chart carries the number.
      height: tallest > 0 ? count / tallest : 0,
      unbounded,
    };
  });
}

/**
 * Which bars get a boundary printed under them.
 *
 * Not all of them: fifteen bounds across a few hundred pixels is unreadable, and a
 * histogram whose axis is a grey smear is worse than one with four legible marks. The
 * first and the last are always among them, because they are the two a reader actually
 * needs — where the distribution starts, and where it stops being bounded at all.
 */
export function axisLabels(bars: readonly HistogramBar[], wanted = 5): (string | null)[] {
  if (bars.length === 0) return [];
  if (bars.length <= wanted) return bars.map((bar) => bar.label);

  const step = (bars.length - 1) / (wanted - 1);
  const marked = new Set(
    Array.from({ length: wanted }, (_unused, index) => Math.round(index * step)),
  );
  return bars.map((bar, index) => (marked.has(index) ? bar.label : null));
}

export interface EstimateReading {
  label: string;
  value: number | null;
}

/**
 * The five estimates, in order, labelled for a reader.
 *
 * Called estimates everywhere they appear because that is what they are: a percentile
 * read off buckets is interpolated inside the bucket the rank falls in, and is exactly
 * as precise as whoever chose the boundaries made it. The screen says so once, near
 * them, rather than in a tooltip nobody opens.
 */
export function estimateReadings(layout: HistogramLayout): EstimateReading[] {
  return [
    { label: "p50", value: layout.p50 },
    { label: "p75", value: layout.p75 },
    { label: "p90", value: layout.p90 },
    { label: "p95", value: layout.p95 },
    { label: "p99", value: layout.p99 },
  ];
}

/**
 * The mean, when the exporter sent a sum to compute it from.
 *
 * Null rather than derived from bucket midpoints when it did not: a mean guessed from
 * boundaries is a guess presented with the same weight as a measurement.
 */
export function meanOf(layout: HistogramLayout): number | null {
  if (layout.sum === null || layout.count <= 0) return null;
  return layout.sum / layout.count;
}
