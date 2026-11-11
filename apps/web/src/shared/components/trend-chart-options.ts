import uPlot from "uplot";

import type { DeployMarker, MarkerCluster } from "@/shared/deploy-markers";
import { clusterMarkers } from "@/shared/deploy-markers";
import type { ChartBand } from "./chart-band";
import { bandStretches } from "./chart-band";

/**
 * `TrendChart`'s uPlot options, as a function of plain values, so they can be tested as
 * data: jsdom has no canvas (F20, design *Chart upgrades*).
 */

export const CHART_HEIGHT = 132;

/** Half of the widest x-axis label, which is what the last tick needs to its right. */
const X_LABEL_OVERHANG = 24;

/** Room above the tallest bucket, so the peak is not clipped by the axis. */
const Y_HEADROOM = 1.1;

/** A y range of [0, 0] draws no axis at all, so a quiet series still gets a scale. */
const MINIMUM_Y_TOP = 1;

const X_TICK_SPACE = 90;
const Y_TICK_SPACE = 32;

const Y_AXIS_WIDTH = 52;
/** Two lines: uPlot puts the clock time on the first and the date under it. */
const X_AXIS_HEIGHT = 40;

/** Written out rather than built, so Tailwind's scanner can see every class. */
/** Solid, then progressively broken: p50 reads as the line, p99 as the edge. */
const DASHES: readonly (number[] | undefined)[] = [undefined, [4, 3], [1, 3]];

export interface ChartPalette {
  line: string;
  axis: string;
  grid: string;
  font: string;
  /** `--color-series-1` … `-6`, probed only for a chart that draws distinct colours. */
  series?: readonly string[];
}

/**
 * Five lines told apart by dash as well as colour, because colour alone is not enough
 * (design-system rule 6). Today's three patterns first, then two more.
 */
const DISTINCT_DASHES: readonly (number[] | undefined)[] = [...DASHES, [8, 3], [6, 2, 1, 2]];

/** Room either side of a fitted range, a share of its span: a gauge is not read from zero. */
const FIT_HEADROOM = Y_HEADROOM - 1;

/** A press and a release are not passed to linked charts: thirty would each select (F20). */
const UNSYNCED_EVENTS: ReadonlySet<string> = new Set(["mousedown", "mouseup"]);

/** What the readout says for a moment with no reading: unknown, never zero. */
export const NO_READING = "—";

/**
 * What a dashboard's chart adds (F20, design *Chart upgrades*). Every one is optional, and
 * with none given the options equal today's, so no other screen changes (AC43, NFR7).
 */
export interface ChartExtras {
  /** The chart's height in pixels, in place of `CHART_HEIGHT`. */
  height?: number;
  /** `"fit"` for a gauge: the y axis follows the readings, with headroom both ways. */
  yRange?: "zero" | "fit";
  /** Each line in its own series colour and dash, for up to five lines of one kind. */
  distinctColours?: boolean;
  /**
   * Called with the reading under the cursor, by index, or null when it leaves. Turns
   * uPlot's own legend off: its labels hide lines on a click and cannot be links.
   */
  readout?: (index: number | null) => void;
  /** Charts sharing a key show one cursor; a press and a release are never shared. */
  syncKey?: string;
  /** A version seen in telemetry is drawn dotted, a reported deploy dashed (D14). */
  dottedSeen?: boolean;
}

/**
 * uPlot's own shape: x values in **seconds**, then one row per series.
 *
 * More than one row means more than one line *on one scale* — which is the
 * point of drawing them together. Three separately-scaled percentile lines
 * would each fill their own chart and look identical; on one scale, a p99
 * pulling away from a p95 is visible, and that is the reading.
 */
export type PlotRows = [xSeconds: number[], ...series: (number | null)[][]];

/** A stretch of the x axis to shade, in uPlot's seconds: a finding's open minutes. */
export interface ChartSpan {
  from: number;
  to: number;
  /** The finding this stretch is, so the readout can name it at the cursor (FR32). */
  id?: string;
  name?: string;
}

/** Faint enough to read the series through, strong enough to see on both themes. */
const SPAN_ALPHA = 0.14;
/** A band is the panel's own hue, fainter still: context for the line, under it. */
const BAND_ALPHA = 0.12;

export interface StripLayout {
  plotLeft: number;
  clusters: MarkerCluster[];
}

/** Each mark's tag, in the plot's CSS pixels, from the chart as it was just drawn. */
function stripOf(chart: uPlot, markers: readonly DeployMarker[]): StripLayout {
  return {
    plotLeft: chart.bbox.left / uPlot.pxRatio,
    clusters: clusterMarkers(markers, (seconds) => chart.valToPos(seconds, "x")),
  };
}

const REPORTED_DASH = [3, 3];
const SEEN_DASH = [1, 3];

/**
 * One dashed vertical rule per release mark, across the plotting area only: the same
 * neutral dash in the axis colour for every one, since a release is context, and the
 * words live in the strip above (FR37).
 *
 * uPlot's own `valToPos` places it, so the rule and the series agree about
 * where a moment is by construction rather than by a second calculation that
 * could drift from it. `releaseMarkers` has already dropped anything outside
 * the window, so nothing here is clamped to an edge.
 */
function drawDeploys(
  chart: uPlot,
  markers: readonly DeployMarker[],
  palette: ChartPalette,
  dottedSeen = false,
): void {
  if (markers.length === 0) return;
  const context = chart.ctx;

  context.save();
  context.beginPath();
  context.rect(chart.bbox.left, chart.bbox.top, chart.bbox.width, chart.bbox.height);
  context.clip();
  context.strokeStyle = palette.axis;
  context.lineWidth = 1;

  for (const marker of markers) {
    context.setLineDash(dottedSeen && marker.source === "seen" ? SEEN_DASH : REPORTED_DASH);
    const x = Math.round(chart.valToPos(marker.at, "x", true)) + 0.5;
    context.beginPath();
    context.moveTo(x, chart.bbox.top);
    context.lineTo(x, chart.bbox.top + chart.bbox.height);
    context.stroke();
  }

  context.restore();
}

/**
 * One shaded band per span, across the plotting area's height, under everything: the
 * stretch a finding was open in is context for the line, as a deploy is.
 */
function drawSpans(chart: uPlot, spans: readonly ChartSpan[], palette: ChartPalette): void {
  if (spans.length === 0) return;
  const context = chart.ctx;

  context.save();
  context.beginPath();
  context.rect(chart.bbox.left, chart.bbox.top, chart.bbox.width, chart.bbox.height);
  context.clip();
  context.fillStyle = palette.axis;
  context.globalAlpha = SPAN_ALPHA;
  for (const span of spans) {
    const left = chart.valToPos(span.from, "x", true);
    const right = chart.valToPos(span.to, "x", true);
    // A span of one minute is still drawn: a finding that short is still a finding.
    context.fillRect(left, chart.bbox.top, Math.max(right - left, 1), chart.bbox.height);
  }
  context.restore();
}

/**
 * The band between two series, filled in the panel's own hue under everything, one
 * shape per unbroken stretch. Drawn here rather than by uPlot's `bands`, which takes a
 * literal colour, so it can reuse the probed line colour at a lower alpha as spans do.
 */
function drawBand(chart: uPlot, band: ChartBand | undefined, palette: ChartPalette): void {
  if (band === undefined) return;
  const stretches = bandStretches(chart.data as PlotRows, band);
  if (stretches.length === 0) return;
  const context = chart.ctx;
  const x = (value: number) => chart.valToPos(value, "x", true);
  const y = (value: number) => chart.valToPos(value, "y", true);

  context.save();
  context.beginPath();
  context.rect(chart.bbox.left, chart.bbox.top, chart.bbox.width, chart.bbox.height);
  context.clip();
  context.fillStyle = palette.line;
  context.globalAlpha = BAND_ALPHA;
  for (const stretch of stretches) {
    context.beginPath();
    stretch.xs.forEach((at, index) => {
      const point: [number, number] = [x(at), y(stretch.upper[index] ?? 0)];
      if (index === 0) context.moveTo(...point);
      else context.lineTo(...point);
    });
    for (let index = stretch.xs.length - 1; index >= 0; index -= 1) {
      context.lineTo(x(stretch.xs[index] ?? 0), y(stretch.lower[index] ?? 0));
    }
    context.closePath();
    context.fill();
  }
  context.restore();
}

/** A drag across the plot, as a range; a click without a drag is not a selection. */
function selectRange(
  chart: uPlot,
  onSelectRange: (fromSeconds: number, toSeconds: number) => void,
): void {
  const { left, width } = chart.select;
  if (width <= 0) return;
  const from = chart.posToVal(left, "x");
  const to = chart.posToVal(left + width, "x");
  chart.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
  if (to > from) onSelectRange(from, to);
}

export function chartOptions(
  palette: ChartPalette,
  width: number,
  formatValue: (value: number) => string,
  labels: readonly string[],
  markers: () => readonly DeployMarker[],
  spans: () => readonly ChartSpan[],
  band: () => ChartBand | undefined,
  onSelectRange?: (fromSeconds: number, toSeconds: number) => void,
  showPoints = false,
  zone?: string,
  onDrawn?: (strip: StripLayout) => void,
  extras: ChartExtras = {},
): uPlot.Options {
  const { readout } = extras;
  return {
    width,
    ...(zone ? { tzDate: (seconds: number) => uPlot.tzDate(new Date(seconds * 1000), zone) } : {}),
    hooks: {
      // Drawn under the series, not over it: a deploy is context for the line,
      // and a rule painted on top of the reading obscures the thing it explains.
      drawClear: [
        (chart) => {
          drawSpans(chart, spans(), palette);
          drawBand(chart, band(), palette);
          drawDeploys(chart, markers(), palette, extras.dottedSeen);
        },
      ],
      setSelect: onSelectRange ? [(chart) => selectRange(chart, onSelectRange)] : [],
      draw: onDrawn ? [(chart) => onDrawn(stripOf(chart, markers()))] : [],
      ...(readout ? { setCursor: [(chart: uPlot) => readout(chart.cursor.idx ?? null)] } : {}),
    },
    height: extras.height ?? CHART_HEIGHT,
    // One series is named by the heading above the chart, so a legend repeats
    // it. Several are not: three duration lines in one colour are three lines
    // nobody can tell apart, and a p99 pulling away from a p95 — the reading
    // the panel exists for — is invisible without knowing which is which.
    legend: { show: readout === undefined && labels.length > 1, live: false },
    // The right padding is the last x label's overhang, not a margin: a tick
    // label is centred on its tick, so the final one loses its right half to the
    // plot's edge without room to sit in — "6:40pm" rendered as "6:40p" and
    // ":00" as ":0C" (`docs/quality/console-review-2026-09-20.md`, D6).
    padding: [8, X_LABEL_OVERHANG, 0, 0],
    cursor: {
      y: false,
      points: { show: readout !== undefined },
      ...(extras.syncKey === undefined
        ? {}
        : {
            sync: {
              key: extras.syncKey,
              filters: { pub: (type: string) => !UNSYNCED_EVENTS.has(type) },
            },
          }),
      // The selection is handed on rather than zooming the chart in place: the
      // screen owns the window, and every panel on it should follow.
      drag: { x: onSelectRange !== undefined, y: false, setScale: false },
    },
    scales: {
      x: { time: true },
      // Zero stays on the axis. A rate chart cropped to its own minimum turns a steady
      // service into a mountain range.
      y:
        extras.yRange === "fit"
          ? { range: (_chart, min, max) => fittedRange(min, max) }
          : { range: (_chart, _min, max) => [0, Math.max(max, MINIMUM_Y_TOP) * Y_HEADROOM] },
    },
    axes: [
      {
        stroke: palette.axis,
        font: palette.font,
        size: X_AXIS_HEIGHT,
        space: X_TICK_SPACE,
        grid: { stroke: palette.grid, width: 1 },
        ticks: { show: false },
        border: { stroke: palette.grid, width: 1 },
      },
      {
        stroke: palette.axis,
        font: palette.font,
        size: Y_AXIS_WIDTH,
        space: Y_TICK_SPACE,
        grid: { stroke: palette.grid, width: 1 },
        ticks: { show: false },
        values: (_chart, splits) => splits.map((value) => formatValue(value)),
      },
    ],
    // One entry per series after the x row. Every line takes the same stroke:
    // the panel's colour says *which panel*, and a second hue inside it would
    // say "different kind of thing" about three views of one measurement.
    // They are told apart by their labels and by where they sit.
    series: [
      {},
      ...labels
        .map((name, index) =>
          extras.distinctColours
            ? distinctLine(name, index, palette, showPoints)
            : {
                label: name,
                stroke: palette.line,
                // One hue for the panel — a second would say "different kind of thing"
                // about three views of one measurement. They are told apart by weight
                // and dash instead: the first line is the emphasised one, because p50
                // is the typical request and the tail is context around it.
                width: index === 0 ? 1.5 : 1,
                dash: DASHES[Math.min(index, DASHES.length - 1)],
                points: { show: showPoints, size: 5, fill: palette.line },
              },
        )
        .map((line) => (readout ? { ...line, value: readingOf(formatValue) } : line)),
    ],
  };
}

/** One of up to five different things, such as five services: its own colour and dash. */
function distinctLine(
  name: string,
  index: number,
  palette: ChartPalette,
  showPoints: boolean,
): uPlot.Series {
  const colours = palette.series ?? [];
  const stroke = colours[index % Math.max(colours.length, 1)] ?? palette.line;
  return {
    label: name,
    stroke,
    width: 1.5,
    dash: DISTINCT_DASHES[index % DISTINCT_DASHES.length],
    points: { show: showPoints, size: 5, fill: stroke },
  };
}

/** A reading in the chart's own unit, as the readout says it; none is a dash. */
function readingOf(formatValue: (value: number) => string) {
  return (_chart: uPlot, value: number | null) =>
    value === null || value === undefined ? NO_READING : formatValue(value);
}

/** The readings' own span, with headroom both ways; a flat line still gets a scale. */
export function fittedRange(min: number, max: number): [number, number] {
  const span = max - min;
  const room = span > 0 ? span * FIT_HEADROOM : Math.abs(max) * FIT_HEADROOM || MINIMUM_Y_TOP;
  return [min - room, max + room];
}
