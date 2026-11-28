import type { ChartSpan, PlotRows, SeriesToken } from "@/shared/components/TrendChart";
import { CHART_HEIGHT } from "@/shared/components/TrendChart";
import type { DeployMarker } from "@/shared/deploy-markers";
import { formatDecimalBytes } from "@/shared/format/bytes";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent, NUMBER_LOCALE, UNKNOWN } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";
import { visibleEscapes } from "@/shared/format/visible-escapes";

import type { MetricAttribute, NodeName, PanelHeight, Series, StoredPanel } from "./api";
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
  uptime_percent: "Uptime",
  mean_duration_ms: "Duration",
  mean_ms: "Mean time",
};

/** Latency merges one quantile state and is an estimate on every rollup panel (D44, AC113). */
const ESTIMATED_READINGS = new Set(["p50_ms", "p95_ms", "p99_ms"]);

const TONES: Record<string, SeriesToken | "critical"> = {
  requests_per_minute: 1,
  error_rate: "critical",
  p50_ms: 2,
  p95_ms: 2,
  p99_ms: 2,
  uptime_percent: 1,
  mean_duration_ms: 2,
  mean_ms: 2,
};

/** Kinds whose lines are one node's or one ranking's each, told apart by colour and dash. */
const NODE_KINDS = new Set(["node_trend", "top_nodes_trend"]);

/** A node's readings by what the metric measures: a share, CPU cores, or bytes. */
function nodeFormat(metric: string): (value: number) => string {
  if (metric === "container.cpu.usage" || metric === "container.cpu.utilization") {
    return (value) => `${formatCount(Number(value.toFixed(2)))} cores`;
  }
  if (metric.endsWith(".utilization")) return (value) => formatPercent(value);
  return (value) => formatDecimalBytes(value);
}

function readingFormat(reading: string): (value: number) => string {
  if (reading === "error_rate" || reading === "uptime_percent") {
    return (value) => `${value.toFixed(1)}%`;
  }
  if (ESTIMATED_READINGS.has(reading) || reading === "mean_duration_ms" || reading === "mean_ms") {
    return (value) => formatDuration(value);
  }
  return (value) => formatRatePerMinute(value);
}

/** A top-nodes line's name: its container, its host, or the service that reported it. */
function nodeLineName(node: NodeName | null | undefined): string {
  if (!node) return "";
  if (node.container) return visibleEscapes(node.container);
  if (node.host) return visibleEscapes(node.host);
  return `reported by ${visibleEscapes(node.reporter)}`;
}

/** A metric reading's own decimals: a gauge may be fractional or below zero, unlike a count. */
const METRIC_DECIMALS = 2;
/** OTel's unit for "a plain number" (UCUM's unity). */
const UNITY = "1";

/**
 * A metric reading in the unit its SDK sent (UCUM, as OTel spells it): a duration, bytes,
 * or a number with its unit written after it, escaped since a unit is customer text.
 * A gauge may fall below zero, so only a non-number is a dash.
 */
export function metricFormat(unit: string): (value: number) => string {
  if (unit === "ms") return (value) => formatDuration(value);
  if (unit === "s") return (value) => formatDuration(value * MS_PER_SECOND);
  if (unit === "By") return (value) => formatDecimalBytes(value);
  const suffix = unit === "" || unit === UNITY ? "" : ` ${visibleEscapes(unit)}`;
  return (value) =>
    Number.isFinite(value)
      ? `${value.toLocaleString(NUMBER_LOCALE, { maximumFractionDigits: METRIC_DECIMALS })}${suffix}`
      : UNKNOWN;
}

/** A chosen series' name: its attributes, each escaped (FR61), or the metric with none. */
export function attributesName(attributes: readonly MetricAttribute[]): string {
  return attributes
    .map(({ key, value }) => `${visibleEscapes(key)}=${visibleEscapes(value)}`)
    .join(", ");
}

/** A metric series' one line: its chosen attributes, or every series combined. */
function metricLineName(panel: StoredPanel, series: Series, combined: number): string {
  const metric = typeof panel.settings.metric === "string" ? panel.settings.metric : "";
  const named = series.attributes && series.attributes.length > 0;
  if (named) return attributesName(series.attributes ?? []);
  if (panel.settings.series !== null && panel.settings.series !== undefined) {
    return visibleEscapes(metric);
  }
  return `${visibleEscapes(metric)} · ${formatCount(combined)} series combined`;
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
  const topNodes = panel.kind === "top_nodes_trend";
  const node = NODE_KINDS.has(panel.kind);
  const metric = panel.kind === "metric_series";
  const combined = reading.series_combined ?? 0;

  return {
    lines: reading.series.map((series) => ({
      name: topN
        ? visibleEscapes(series.service ?? "")
        : topNodes
          ? nodeLineName(series.node)
          : node
            ? series.label
            : metric
              ? metricLineName(panel, series, combined)
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
    format: metric
      ? metricFormat(reading.units?.[0] ?? "")
      : node
        ? nodeFormat(reading0)
        : readingFormat(reading0),
    tone: node ? 2 : (TONES[reading0] ?? 1),
    distinctColours: topN || topNodes,
    // A node's reading and a gauge are levels, not counts: read off zero (design, *Chart
    // upgrades*). A sum's line is what it rose, which starts from zero.
    yRange: node || (metric && panel.settings.shape === "gauge") ? "fit" : "zero",
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
