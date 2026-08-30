import type { TrendPoint } from "./api";

/**
 * Turning one service's history into what a chart draws.
 *
 * Three charts rather than one: request rate, failure share and latency are three
 * different units, and a single y axis would either flatten two of them or lie about
 * the third. uPlot has no stacking either way, so nothing here accumulates — each row
 * is the value the API reported for that bucket.
 */

/**
 * uPlot's aligned-data shape, spelled out so this module stays free of the library.
 * One x row in seconds, then the values drawn against it.
 */
export type PlotRows = [xSeconds: number[], values: number[]];

export type TrendMetric = "rate" | "errorRate" | "latency";

/** The chart palette tokens, `--color-series-1` … `-6`. */
export type SeriesToken = 1 | 2 | 3 | 4 | 5 | 6;

export interface TrendPlot {
  metric: TrendMetric;
  /** The heading over the chart. */
  label: string;
  /** What the numbers are, for the reader and for assistive technology. */
  unit: string;
  seriesToken: SeriesToken | "critical";
  data: PlotRows;
  /**
   * Every bucket read zero. A line pinned to the axis is indistinguishable from a
   * broken chart, so the screen says it in words instead of drawing it.
   */
  flatAtZero: boolean;
  /** Those words. What a flat zero means for this particular metric. */
  zeroText: string;
}

const MS_PER_SECOND = 1000;
const PERCENT = 100;

/**
 * Failure share is drawn in the critical tone rather than a palette hue: it is the one
 * of the three where a rise is bad, and colour should say so.
 */
const METRICS: ReadonlyArray<{
  metric: TrendMetric;
  label: string;
  unit: string;
  seriesToken: SeriesToken | "critical";
  zeroText: string;
  read: (point: TrendPoint) => number;
}> = [
  {
    metric: "rate",
    label: "Request rate",
    unit: "requests a minute",
    seriesToken: 1,
    zeroText: "No requests in this window.",
    read: (point) => point.requests_per_minute,
  },
  {
    metric: "errorRate",
    label: "Failure share",
    unit: "percent of requests failing",
    seriesToken: "critical",
    zeroText: "Nothing failed in this window.",
    // A fraction on the wire; the axis is read in percent.
    read: (point) => point.error_rate * PERCENT,
  },
  {
    metric: "latency",
    label: "p95 latency",
    unit: "milliseconds",
    seriesToken: 3,
    zeroText: "No latency recorded in this window.",
    read: (point) => point.p95_ms,
  },
];

/**
 * One plot per metric, oldest bucket first.
 *
 * Buckets whose timestamp will not parse are dropped rather than plotted at the epoch,
 * where one bad row drags the whole x axis back to 1970.
 */
export function buildTrendPlots(points: readonly TrendPoint[]): TrendPlot[] {
  const usable = points.filter((point) => Number.isFinite(Date.parse(point.bucket)));
  const seconds = usable.map((point) => Date.parse(point.bucket) / MS_PER_SECOND);

  return METRICS.map((descriptor) => {
    const values = usable.map((point) => descriptor.read(point));
    return {
      metric: descriptor.metric,
      label: descriptor.label,
      unit: descriptor.unit,
      seriesToken: descriptor.seriesToken,
      data: [seconds, values] as PlotRows,
      flatAtZero: values.length > 0 && values.every((value) => value === 0),
      zeroText: descriptor.zeroText,
    };
  });
}

/** What assistive technology is read in place of a canvas, which says nothing at all. */
export function describePlot(plot: TrendPlot, serviceName: string, bucketCount: number): string {
  if (bucketCount === 0) return `${plot.label} for ${serviceName}: no buckets in this window.`;
  if (plot.flatAtZero) return `${plot.label} for ${serviceName}: zero across every bucket.`;
  return `${plot.label} for ${serviceName}, in ${plot.unit}, across ${bucketCount} buckets of the selected window.`;
}
