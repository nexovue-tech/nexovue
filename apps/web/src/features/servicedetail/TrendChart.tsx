import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./trend-chart.css";

import { useEffect, useRef } from "react";

import { useTheme } from "@/shared/theme/use-theme";

import type { TrendPlot } from "./trend-series";

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
const SERIES_TEXT_CLASS: Record<TrendPlot["seriesToken"], string> = {
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

export interface TrendChartProps {
  plot: TrendPlot;
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

function TrendCanvas({ plot, formatValue, description }: TrendChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);
  const latest = useRef({ plot, formatValue });

  useEffect(() => {
    latest.current = { plot, formatValue };
  }, [plot, formatValue]);

  useEffect(() => {
    const host = hostRef.current;
    const probe = probeRef.current;
    if (!host || !probe) return;

    const chart = new uPlot(
      chartOptions(
        readPalette(probe),
        host.clientWidth,
        (value) => latest.current.formatValue(value),
        latest.current.plot.label,
      ),
      latest.current.plot.data,
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
    chartRef.current?.setData(plot.data);
  }, [plot]);

  return (
    <div className={`nv-trend-chart ${SERIES_TEXT_CLASS[plot.seriesToken]}`}>
      {/* The chart's colours, worn by real elements so the token layer stays the only
          place a colour is decided. Sized to nothing rather than hidden: display:none
          has no computed colour to read. */}
      <div ref={probeRef} aria-hidden="true" className="h-0 overflow-hidden">
        <span data-probe="axis" className="font-mono text-2xs text-ink-dim" />
        <span data-probe="grid" className="text-line-subtle" />
        <span data-probe="line" className={SERIES_TEXT_CLASS[plot.seriesToken]} />
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

function chartOptions(
  palette: ChartPalette,
  width: number,
  formatValue: (value: number) => string,
  label: string,
): uPlot.Options {
  return {
    width,
    height: CHART_HEIGHT,
    // The heading above the chart names the series; a legend would repeat it.
    legend: { show: false },
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
    series: [{}, { label, stroke: palette.line, width: 1.5, points: { show: false } }],
  };
}
