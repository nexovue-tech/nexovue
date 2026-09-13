import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./trend-chart.css";

import { useEffect, useMemo, useRef } from "react";
import type { DeployMarker } from "@/shared/deploy-markers";
import { useTheme } from "@/shared/theme/use-theme";

/**
 * One metric of one service, over the window, drawn with uPlot.
 *
 * Colours come out of the DOM rather than out of this file. The token layer is CSS
 * custom properties that swap per theme, and a canvas needs a literal colour string —
 * so the probe below wears the same token classes the rest of the screen does and the
 * chart reads back whatever the browser painted. One palette, in one place, both themes.
 */

export const CHART_HEIGHT = 132;

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

const SERIES_TEXT_CLASS: Record<SeriesToken | "critical", string> = {
  1: "text-series-1",
  2: "text-series-2",
  3: "text-series-3",
  4: "text-series-4",
  5: "text-series-5",
  6: "text-series-6",
  critical: "text-critical-text",
};

interface ChartPalette {
  line: string;
  axis: string;
  grid: string;
  font: string;
}

/** The chart palette tokens, `--color-series-1` … `-6`. */
export type SeriesToken = 1 | 2 | 3 | 4 | 5 | 6;

/**
 * uPlot's own shape: x values in **seconds**, then one row per series.
 *
 * More than one row means more than one line *on one scale* — which is the
 * point of drawing them together. Three separately-scaled percentile lines
 * would each fill their own chart and look identical; on one scale, a p99
 * pulling away from a p95 is visible, and that is the reading.
 */
export type PlotRows = [xSeconds: number[], ...series: number[][]];

export interface TrendChartProps {
  /**
   * One name per series, in `data`'s own order. A single-series chart passes
   * one; the duration panel passes three.
   */
  label: string | readonly string[];
  seriesToken: SeriesToken | "critical";
  data: PlotRows;
  /** Vertical rules across the plot — the deploys inside this window (IA3). */
  markers?: readonly DeployMarker[];
  /** How a y value is written on the axis — the metric's own unit, not a raw number. */
  formatValue: (value: number) => string;
  /** Read by assistive technology in place of the canvas. */
  description: string;
}

/**
 * A uPlot instance is imperative and cannot be re-configured in place, so its identity
 * is bound to the theme it was drawn in. New readings flow through `setData`, which
 * keeps the cursor alive; a theme change remounts instead of patching.
 */
export function TrendChart(props: TrendChartProps) {
  const { resolved } = useTheme();
  return <TrendCanvas key={resolved} {...props} />;
}

function TrendCanvas({
  label,
  seriesToken,
  data,
  markers = [],
  formatValue,
  description,
}: TrendChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);
  const labels = useMemo(() => (Array.isArray(label) ? label : [label as string]), [label]);
  const latest = useRef({ labels, data, formatValue, markers });

  useEffect(() => {
    latest.current = { labels, data, formatValue, markers };
  }, [labels, data, formatValue, markers]);

  // Markers are read through the ref rather than closed over, so a deploy
  // arriving after the chart was built is drawn on the next frame instead of
  // remounting the whole plot and losing the cursor.
  useEffect(() => {
    chartRef.current?.redraw();
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    const probe = probeRef.current;
    if (!host || !probe) return;

    const chart = new uPlot(
      chartOptions(
        readPalette(probe),
        host.clientWidth,
        (value) => latest.current.formatValue(value),
        latest.current.labels,
        () => latest.current.markers,
      ),
      latest.current.data,
      host,
    );
    chartRef.current = chart;

    const observer = new ResizeObserver(([entry]) => {
      chart.setSize({ width: Math.round(entry.contentRect.width), height: CHART_HEIGHT });
    });
    observer.observe(host);

    return () => {
      observer.disconnect();
      chart.destroy();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setData(data);
  }, [data]);

  return (
    <div className={`nv-trend-chart ${SERIES_TEXT_CLASS[seriesToken]}`}>
      {/* The chart's colours, worn by real elements so the token layer stays the only
          place a colour is decided. Sized to nothing rather than hidden: display:none
          has no computed colour to read. */}
      <div ref={probeRef} aria-hidden="true" className="h-0 overflow-hidden">
        <span data-probe="axis" className="font-mono text-2xs text-ink-dim" />
        <span data-probe="grid" className="text-line-subtle" />
        <span data-probe="line" className={SERIES_TEXT_CLASS[seriesToken]} />
      </div>

      <div ref={hostRef} role="img" aria-label={description} className="w-full" />
    </div>
  );
}

function readPalette(probe: HTMLElement): ChartPalette {
  const axis = probeElement(probe, "axis");
  const axisStyle = getComputedStyle(axis);

  return {
    line: colourOf(probeElement(probe, "line")),
    axis: axisStyle.color,
    grid: colourOf(probeElement(probe, "grid")),
    // The `font` shorthand computes to an empty string once font-variant is set, which
    // this app sets globally for tabular numerals. The two parts a canvas needs survive.
    font: `${axisStyle.fontSize} ${axisStyle.fontFamily}`,
  };
}

function probeElement(probe: HTMLElement, name: string): HTMLElement {
  const found = probe.querySelector<HTMLElement>(`[data-probe="${name}"]`);
  if (!found) throw new Error(`TrendChart is missing its ${name} colour probe`);
  return found;
}

function colourOf(element: HTMLElement): string {
  return getComputedStyle(element).color;
}

/**
 * One dashed vertical rule per deploy, across the plotting area only.
 *
 * uPlot's own `valToPos` places it, so the rule and the series agree about
 * where a moment is by construction rather than by a second calculation that
 * could drift from it. `deployMarkers` has already dropped anything outside
 * the window, so nothing here is clamped to an edge.
 */
function drawDeploys(chart: uPlot, markers: readonly DeployMarker[], palette: ChartPalette): void {
  if (markers.length === 0) return;
  const context = chart.ctx;

  context.save();
  context.beginPath();
  context.rect(chart.bbox.left, chart.bbox.top, chart.bbox.width, chart.bbox.height);
  context.clip();
  context.strokeStyle = palette.axis;
  context.lineWidth = 1;
  context.setLineDash([3, 3]);

  for (const marker of markers) {
    const x = Math.round(chart.valToPos(marker.at, "x", true)) + 0.5;
    context.beginPath();
    context.moveTo(x, chart.bbox.top);
    context.lineTo(x, chart.bbox.top + chart.bbox.height);
    context.stroke();
  }

  context.restore();
}

function chartOptions(
  palette: ChartPalette,
  width: number,
  formatValue: (value: number) => string,
  labels: readonly string[],
  markers: () => readonly DeployMarker[],
): uPlot.Options {
  return {
    width,
    // Drawn under the series, not over it: a deploy is context for the line,
    // and a rule painted on top of the reading obscures the thing it explains.
    hooks: { drawClear: [(chart) => drawDeploys(chart, markers(), palette)] },
    height: CHART_HEIGHT,
    // One series is named by the heading above the chart, so a legend repeats
    // it. Several are not: three duration lines in one colour are three lines
    // nobody can tell apart, and a p99 pulling away from a p95 — the reading
    // the panel exists for — is invisible without knowing which is which.
    legend: { show: labels.length > 1, live: false },
    padding: [8, 8, 0, 0],
    cursor: { y: false, points: { show: false }, drag: { x: false, y: false } },
    scales: {
      x: { time: true },
      // Zero stays on the axis. A rate chart cropped to its own minimum turns a steady
      // service into a mountain range.
      y: {
        range: (_chart, _min, max) => [0, Math.max(max, MINIMUM_Y_TOP) * Y_HEADROOM],
      },
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
      ...labels.map((name, index) => ({
        label: name,
        stroke: palette.line,
        // One hue for the panel — a second would say "different kind of thing"
        // about three views of one measurement. They are told apart by weight
        // and dash instead: the first line is the emphasised one, because p50
        // is the typical request and the tail is context around it.
        width: index === 0 ? 1.5 : 1,
        dash: DASHES[Math.min(index, DASHES.length - 1)],
        points: { show: false },
      })),
    ],
  };
}
