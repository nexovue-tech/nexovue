import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./phase-chart.css";

import { useEffect, useRef } from "react";

import { formatDuration } from "@/shared/format/duration";
import { useTheme } from "@/shared/theme/use-theme";

import type { PhasePlot, SeriesToken } from "./phase-series";

/**
 * The stacked phase breakdown, drawn with uPlot.
 *
 * uPlot has no stacking of its own — the author declines it on principle — so the
 * accumulation happens in `phase-series.ts` and this file only draws. Each series is a
 * running total filled to the baseline, outermost first, so every later fill paints the
 * band belonging to its own phase.
 *
 * Colours come out of the DOM rather than out of this file. The token layer is CSS
 * custom properties that swap per theme, `@theme inline` does not publish them under
 * their semantic names, and a canvas needs a literal colour string — so the probe below
 * wears the same token classes the rest of the screen does and the chart reads back
 * whatever the browser painted. That keeps one palette in one place, in both themes,
 * with no colour written into a component.
 */

const CHART_HEIGHT = 224;

/** Room above the tallest bucket, so the top band is not clipped by the axis. */
const Y_HEADROOM = 1.08;

/** Minimum pixels between gridlines. Wider than the default: this is a dense screen. */
const X_TICK_SPACE = 80;
const Y_TICK_SPACE = 40;

const Y_AXIS_WIDTH = 56;
/** Two lines: uPlot puts the clock time on the first and the date under it. */
const X_AXIS_HEIGHT = 44;

/** Written out rather than built, so Tailwind's scanner can see every class. */
const SERIES_TEXT_CLASS: Record<SeriesToken, string> = {
  1: "text-series-1",
  2: "text-series-2",
  3: "text-series-3",
  4: "text-series-4",
  5: "text-series-5",
  6: "text-series-6",
};

interface ChartPalette {
  byPhase: Map<string, string>;
  axis: string;
  grid: string;
  page: string;
  font: string;
}

export interface PhaseChartProps {
  plot: PhasePlot;
  /** Bucket under the cursor, or null when the pointer leaves the plot. */
  onHoverBucket: (bucketIndex: number | null) => void;
  /** Read by assistive technology in place of the canvas, which says nothing. */
  description: string;
}

/**
 * A uPlot instance is imperative and cannot be re-configured in place, so its identity
 * is bound to the chart's *composition* — the phases drawn and the theme they are drawn
 * in. Expressing that as a React key means new readings flow through `setData`, which
 * keeps the cursor alive, and a genuine change of shape remounts instead of patching.
 */
export function PhaseChart(props: PhaseChartProps) {
  const { resolved } = useTheme();
  const composition = `${resolved}:${props.plot.drawn.map((series) => series.phase).join(",")}`;

  return <PhaseCanvas key={composition} {...props} />;
}

function PhaseCanvas({ plot, onHoverBucket, description }: PhaseChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);
  const latestPlot = useRef(plot);
  const latestHover = useRef(onHoverBucket);

  useEffect(() => {
    latestPlot.current = plot;
    latestHover.current = onHoverBucket;
  }, [plot, onHoverBucket]);

  useEffect(() => {
    const host = hostRef.current;
    const probe = probeRef.current;
    if (!host || !probe) return;

    const palette = readPalette(probe, latestPlot.current);
    const chart = new uPlot(
      chartOptions(latestPlot.current, palette, host.clientWidth, (index) =>
        latestHover.current(index),
      ),
      latestPlot.current.data,
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
    <div className="nv-phase-chart text-line-strong">
      {/* The chart's colours, worn by real elements so the token layer stays the only
          place a colour is decided. Sized to nothing rather than hidden: display:none
          has no computed colour to read. */}
      <div ref={probeRef} aria-hidden="true" className="h-0 overflow-hidden">
        <span data-probe="axis" className="font-mono text-2xs text-ink-dim" />
        <span data-probe="grid" className="text-line-subtle" />
        <span data-probe="page" className="text-page" />
        {plot.drawn.map((series) => (
          <span
            key={series.phase}
            data-probe={`phase:${series.phase}`}
            className={SERIES_TEXT_CLASS[series.seriesToken]}
          />
        ))}
      </div>

      <div ref={hostRef} role="img" aria-label={description} className="w-full" />
    </div>
  );
}

function readPalette(probe: HTMLElement, plot: PhasePlot): ChartPalette {
  const axis = probeElement(probe, "axis");
  const axisStyle = getComputedStyle(axis);

  return {
    byPhase: new Map(
      plot.drawn.map((series) => [
        series.phase,
        colourOf(probeElement(probe, `phase:${series.phase}`)),
      ]),
    ),
    axis: axisStyle.color,
    grid: colourOf(probeElement(probe, "grid")),
    page: colourOf(probeElement(probe, "page")),
    // The `font` shorthand computes to an empty string once font-variant is set, which
    // this app sets globally for tabular numerals. The two parts a canvas needs survive.
    font: `${axisStyle.fontSize} ${axisStyle.fontFamily}`,
  };
}

function probeElement(probe: HTMLElement, name: string): HTMLElement {
  const found = probe.querySelector<HTMLElement>(`[data-probe="${name}"]`);
  if (!found) throw new Error(`PhaseChart is missing its ${name} colour probe`);
  return found;
}

function colourOf(element: HTMLElement): string {
  return getComputedStyle(element).color;
}

function chartOptions(
  plot: PhasePlot,
  palette: ChartPalette,
  width: number,
  onHover: (bucketIndex: number | null) => void,
): uPlot.Options {
  return {
    width,
    height: CHART_HEIGHT,
    // The values are drawn stacked; the legend beside the chart carries the numbers.
    legend: { show: false },
    padding: [8, 8, 0, 0],
    cursor: {
      y: false,
      points: { show: false },
      drag: { x: false, y: false },
    },
    scales: {
      x: { time: true },
      // Zero has to be on the axis: a stack that starts partway up is a lie about shares.
      y: { range: (_chart, _min, max) => [0, Math.max(max, 1) * Y_HEADROOM] },
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
        values: (_chart, splits) => splits.map((value) => formatDuration(value)),
      },
    ],
    series: [
      {},
      ...plot.drawn.map((series, index) => ({
        label: series.phase,
        fill: palette.byPhase.get(series.phase),
        // The outermost band keeps its own edge; every band under it is separated from
        // the one above by a hairline in the page colour, so two hues never smear.
        stroke: index === 0 ? palette.byPhase.get(series.phase) : palette.page,
        width: 1,
        points: { show: false },
      })),
    ],
    hooks: {
      setCursor: [(chart) => onHover(chart.cursor.idx ?? null)],
    },
  };
}
