import type { ChartSpan, PlotRows, SeriesToken } from "@/shared/components/TrendChart";
import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import type { DeployMarker } from "@/shared/deploy-markers";
import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";
import { visibleEscapes } from "@/shared/format/visible-escapes";

import type { PanelHeight, StoredPanel } from "./api";
import type { OkResult } from "./kept-readings";

/**
 * What a time panel draws, worked out from its stored panel and its reading as plain
 * data, so it is tested without a canvas (F20, T4.4 to T4.9). `TimePanel` hands this to
 * `TrendChart`, `ChartLegend` and `ChartTable`.
 */

const MS_PER_SECOND = 1000;
const PERCENT = 100;

/** `large` is 240 px (*guess*, spec FR5); `medium` is the chart's own height. */
export const LARGE_CHART_HEIGHT = 240;

const READING_NAMES: Record<string, string> = {
  requests_per_minute: "Requests/min",
  error_rate: "Failure share",
  p50_ms: "p50",
  p95_ms: "p95",
  p99_ms: "p99",
};

/** Latency merges one quantile state and is an estimate on every rollup panel (D44, AC113). */
const ESTIMATED_READINGS = new Set(["p50_ms", "p95_ms", "p99_ms"]);

const TONES: Record<string, SeriesToken | "critical"> = {
  requests_per_minute: 1,
  error_rate: "critical",
  p50_ms: 2,
  p95_ms: 2,
  p99_ms: 2,
};

/** A node's readings by what the metric measures: a share, CPU cores, or bytes. */
function nodeFormat(metric: string): (value: number) => string {
  if (metric === "container.cpu.usage" || metric === "container.cpu.utilization") {
    return (value) => `${formatCount(Number(value.toFixed(2)))} cores`;
  }
  if (metric.endsWith(".utilization")) return (value) => formatPercent(value);
  return (value) => formatDecimalBytes(value);
}

function readingFormat(reading: string): (value: number) => string {
  if (reading === "error_rate") return (value) => `${value.toFixed(1)}%`;
  if (ESTIMATED_READINGS.has(reading)) return (value) => formatDuration(value);
  return (value) => formatRatePerMinute(value);
}

/** One drawn line: what it is called, the service it links to, and its readings. */
export interface ChartLine {
  name: string;
  /** The raw service name a top-N line opens; its name is the escaped form (FR35, FR61). */
  service?: string;
  /** Marked "(est.)" wherever its number is written (AC113). */
  estimate: boolean;
}

export interface TimeChart {
  lines: ChartLine[];
  /** The bucket starts drawn, in seconds, without the bucket still filling (FR66). */
  x: number[];
  /** One row per line, aligned to `x`. A gap is null, never zero. */
  values: (number | null)[][];
  /** The bucket still filling, kept off the line, read as "so far" (FR66, AC114). */
  soFar: { at: number; values: (number | null)[] } | undefined;
  bucketSeconds: number;
  format: (value: number) => string;
  tone: SeriesToken | "critical";
  distinctColours: boolean;
  yRange: "zero" | "fit";
  height: number;
}

export function chartHeight(height: PanelHeight): number {
  return height === "large" ? LARGE_CHART_HEIGHT : CHART_HEIGHT;
}

/** A top-N chart and a node chart are their own; every other time panel is one reading's views. */
export function timeChart(panel: StoredPanel, reading: OkResult): TimeChart {
  const starts = [
    ...new Set(reading.series.flatMap((series) => series.points.map(([at]) => Date.parse(at)))),
  ].sort((first, second) => first - second);
  const filling = reading.incomplete_from ? Date.parse(reading.incomplete_from) : undefined;
  const drawn = starts.filter((at) => filling === undefined || at < filling);
  const valueAt = (points: [string, number][], at: number) => {
    const found = points.find(([when]) => Date.parse(when) === at);
    return found === undefined ? null : scaled(panel, found[1]);
  };
  const [first] = reading.series;
  const reading0 = first?.label ?? "";
  const topN = panel.kind === "top_services_trend";
  const node = panel.kind === "node_trend";

  return {
    lines: reading.series.map((series) => ({
      name: topN
        ? visibleEscapes(series.service ?? "")
        : node
          ? series.label
          : (READING_NAMES[series.label] ?? series.label),
      ...(topN && series.service !== undefined && series.service !== null
        ? { service: series.service }
        : {}),
      estimate: ESTIMATED_READINGS.has(series.label),
    })),
    x: drawn.map((at) => at / MS_PER_SECOND),
    values: reading.series.map((series) => drawn.map((at) => valueAt(series.points, at))),
    soFar:
      filling === undefined || !starts.includes(filling)
        ? undefined
        : {
            at: filling / MS_PER_SECOND,
            values: reading.series.map((series) => valueAt(series.points, filling)),
          },
    bucketSeconds: reading.bucket_seconds,
    format: node ? nodeFormat(reading0) : readingFormat(reading0),
    tone: node ? 2 : (TONES[reading0] ?? 1),
    distinctColours: topN,
    // A node's reading is a level, not a count: read off zero (design, *Chart upgrades*).
    yRange: node ? "fit" : "zero",
    height: chartHeight(panel.height),
  };
}

/** The failure share travels as a fraction and is drawn in percent, as Overview draws it. */
function scaled(panel: StoredPanel, value: number): number {
  return panel.settings.signal === "errors" && panel.kind !== "node_trend"
    ? value * PERCENT
    : value;
}

export function plotRows(chart: TimeChart): PlotRows {
  return [chart.x, ...chart.values];
}

/** What sits at one moment: the finding open then, and each release in that bucket. */
export interface Marks {
  spans: readonly ChartSpan[];
  markers: readonly DeployMarker[];
}

export const NO_MARKS: Marks = { spans: [], markers: [] };

/** The words for what is at a bucket: "Finding: …", "Deploy v2.1", or a version only seen. */
export function marksAt(marks: Marks, at: number, bucketSeconds: number): string[] {
  const findings = marks.spans
    .filter((span) => span.from < at + bucketSeconds && span.to >= at && span.name)
    .map((span) => `Finding: ${visibleEscapes(span.name ?? "")}`);
  const releases = marks.markers
    .filter((marker) => marker.at >= at && marker.at < at + bucketSeconds)
    .map((marker) =>
      marker.source === "seen"
        ? `Version seen in telemetry ${visibleEscapes(marker.version)}`
        : `Deploy ${visibleEscapes(marker.version)}`,
    );
  return [...findings, ...releases];
}

/** One cursor across every chart on a dashboard (FR32). */
export function syncKeyOf(dashboardId: string): string {
  return `dashboard-${dashboardId}`;
}
