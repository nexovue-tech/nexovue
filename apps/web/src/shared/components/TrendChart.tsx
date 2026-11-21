import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./trend-chart.css";

import { useEffect, useMemo, useRef, useState } from "react";
import type { DeployMarker } from "@/shared/deploy-markers";
import { useTheme } from "@/shared/theme/use-theme";
import { ReleaseStrip } from "./ChartReleases";
import type { ChartBand } from "./chart-band";
import type {
  ChartExtras,
  ChartPalette,
  ChartSpan,
  PlotRows,
  StripLayout,
} from "./trend-chart-options";
import { CHART_HEIGHT, chartOptions } from "./trend-chart-options";

export type { ChartSpan, PlotRows } from "./trend-chart-options";
export { CHART_HEIGHT } from "./trend-chart-options";

/**
 * One metric of one service, over the window, drawn with uPlot.
 *
 * Colours come out of the DOM rather than out of this file. The token layer is CSS
 * custom properties that swap per theme, and a canvas needs a literal colour string —
 * so the probe below wears the same token classes the rest of the screen does and the
 * chart reads back whatever the browser painted. One palette, in one place, both themes.
 */

const SERIES_TEXT_CLASS: Record<SeriesToken | "critical", string> = {
  1: "text-series-1",
  2: "text-series-2",
  3: "text-series-3",
  4: "text-series-4",
  5: "text-series-5",
  6: "text-series-6",
  critical: "text-critical-text",
};

/** The chart palette tokens, `--color-series-1` … `-6`. */
export type SeriesToken = 1 | 2 | 3 | 4 | 5 | 6;

/** The six chart tokens, probed only for a chart drawing several things in distinct colours. */
const SERIES_TOKENS: readonly SeriesToken[] = [1, 2, 3, 4, 5, 6];

// One instance each, so a chart with none does not redraw on every render for a new [].
const NO_MARKERS: readonly DeployMarker[] = [];
const NO_SPANS: readonly ChartSpan[] = [];

export interface TrendChartProps {
  /**
   * One name per series, in `data`'s own order. A single-series chart passes
   * one; the duration panel passes three.
   */
  label: string | readonly string[];
  seriesToken: SeriesToken | "critical";
  data: PlotRows;
  /**
   * Release marks inside this window (IA3, F28): one neutral dash each on the canvas,
   * and a tag above the plot carrying the words, the hover and the pinned box.
   */
  markers?: readonly DeployMarker[];
  /** Shaded stretches under the series — the findings open inside this window (F6). */
  spans?: readonly ChartSpan[];
  /** A filled band between two of `data`'s series — a finding's normal band (AC38). */
  band?: ChartBand;
  /** How a y value is written on the axis — the metric's own unit, not a raw number. */
  formatValue: (value: number) => string;
  /** Read by assistive technology in place of the canvas. */
  description: string;
  /**
   * Dragging across the chart selects a range, handed over in epoch seconds. Absent,
   * the chart cannot be dragged — a selection that does nothing is a broken promise.
   */
  onSelectRange?: (fromSeconds: number, toSeconds: number) => void;
  /**
   * Draws each reading as a dot as well as a line. For sparse series: a reading with
   * a gap on both sides has no neighbour to draw a line to, and is otherwise invisible.
   */
  showPoints?: boolean;
  /** The IANA zone the time axis reads in, when the sentences around it name one; else the browser's. */
  zone?: string;
  /** The chart's height in pixels; `CHART_HEIGHT` when absent (F20, `large` panels). */
  height?: number;
  /** `"fit"` for a gauge, read off zero; from zero when absent. */
  yRange?: ChartExtras["yRange"];
  /** Each line in its own series colour and dash: up to five services, never one reading's views. */
  distinctColours?: boolean;
  /**
   * The reading under the cursor, by index, or null when it leaves, for a readout the
   * caller draws as text and links. uPlot's own legend is then off (F20, FR32).
   */
  readout?: (index: number | null) => void;
  /** Charts sharing a key show one cursor at one moment; a drag stays on its own chart. */
  syncKey?: string;
  /** A version seen in telemetry drawn dotted, a reported deploy dashed (F20, D14). */
  dottedSeen?: boolean;
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
  markers = NO_MARKERS,
  spans = NO_SPANS,
  band,
  formatValue,
  description,
  onSelectRange,
  showPoints = false,
  zone,
  height = CHART_HEIGHT,
  yRange,
  distinctColours = false,
  readout,
  syncKey,
  dottedSeen = false,
}: TrendChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<uPlot | null>(null);
  const labels = useMemo(() => (Array.isArray(label) ? label : [label as string]), [label]);
  const latest = useRef({
    labels,
    data,
    formatValue,
    markers,
    spans,
    band,
    onSelectRange,
    readout,
  });
  // uPlot's options cannot change in place, so becoming draggable rebuilds the chart.
  const selectable = onSelectRange !== undefined;
  const reads = readout !== undefined;
  // Where each mark's tag sits, read from the chart after every draw.
  const [strip, setStrip] = useState<StripLayout>(NO_STRIP);

  // Everything is read through the ref rather than closed over, so a deploy or a
  // finding arriving after the chart was built is drawn by a redraw instead of
  // remounting the whole plot and losing the cursor. A caller's inline formatter is
  // a new function each render, and needs no redraw: the axis reads the ref.
  useEffect(() => {
    latest.current = { labels, data, formatValue, markers, spans, band, onSelectRange, readout };
  });
  const bandKey = band === undefined ? "" : `${band.upper}:${band.lower}`;
  // biome-ignore lint/correctness/useExhaustiveDependencies: what the overlays draw is read through the ref; these are when to draw again.
  useEffect(() => {
    chartRef.current?.redraw();
  }, [markers, spans, bandKey]);

  useEffect(() => {
    const host = hostRef.current;
    const probe = probeRef.current;
    if (!host || !probe) return;

    const chart = new uPlot(
      chartOptions(
        readPalette(probe, distinctColours),
        host.clientWidth,
        (value) => latest.current.formatValue(value),
        latest.current.labels,
        () => latest.current.markers,
        () => latest.current.spans,
        () => latest.current.band,
        selectable ? (from, to) => latest.current.onSelectRange?.(from, to) : undefined,
        showPoints,
        zone,
        (drawn) => setStrip((before) => sameStrip(before, drawn) ?? drawn),
        {
          ...(height === CHART_HEIGHT ? {} : { height }),
          ...(yRange === undefined ? {} : { yRange }),
          ...(distinctColours ? { distinctColours } : {}),
          ...(reads ? { readout: (index) => latest.current.readout?.(index) } : {}),
          ...(syncKey === undefined ? {} : { syncKey }),
          ...(dottedSeen ? { dottedSeen } : {}),
        },
      ),
      latest.current.data,
      host,
    );
    chartRef.current = chart;

    const observer = new ResizeObserver(([entry]) => {
      chart.setSize({ width: Math.round(entry.contentRect.width), height });
    });
    observer.observe(host);
    const untouch = reads ? followTouch(chart) : undefined;

    return () => {
      untouch?.();
      observer.disconnect();
      chart.destroy();
      chartRef.current = null;
    };
  }, [selectable, showPoints, zone, height, yRange, distinctColours, reads, syncKey, dottedSeen]);

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
        {distinctColours
          ? SERIES_TOKENS.map((token) => (
              <span
                key={token}
                data-probe={`series-${token}`}
                className={SERIES_TEXT_CLASS[token]}
              />
            ))
          : null}
      </div>

      {markers.length > 0 ? (
        <ReleaseStrip clusters={strip.clusters} plotLeft={strip.plotLeft} zone={zone} />
      ) : null}
      <div ref={hostRef} role="img" aria-label={description} className="w-full" />
    </div>
  );
}

const NO_STRIP: StripLayout = { plotLeft: 0, clusters: [] };

/** The layout before, when a draw placed every tag where it already was: no re-render. */
function sameStrip(before: StripLayout, drawn: StripLayout): StripLayout | undefined {
  const key = (layout: StripLayout) =>
    `${layout.plotLeft}|${layout.clusters
      .map((cluster) => `${cluster.left}:${cluster.markers.map((m) => m.key).join(",")}`)
      .join(";")}`;
  return key(before) === key(drawn) ? before : undefined;
}

function readPalette(probe: HTMLElement, distinctColours: boolean): ChartPalette {
  const axis = probeElement(probe, "axis");
  const axisStyle = getComputedStyle(axis);

  return {
    ...(distinctColours
      ? {
          series: SERIES_TOKENS.map((token) => colourOf(probeElement(probe, `series-${token}`))),
        }
      : {}),
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
 * A touch moves the cursor, so a phone can read a value: uPlot's own file has no touch
 * handling. **Not tried on a phone** (plan T4.10); where it fails, "View as table" is the
 * route to exact values (FR50). Only on a coarse pointer, so a mouse is untouched.
 */
function followTouch(chart: uPlot): (() => void) | undefined {
  if (!window.matchMedia("(pointer: coarse)").matches) return undefined;
  const plot = chart.over;
  const move = (event: TouchEvent) => {
    const touch = event.touches[0];
    if (!touch) return;
    const box = plot.getBoundingClientRect();
    chart.setCursor({ left: touch.clientX - box.left, top: touch.clientY - box.top });
  };
  plot.addEventListener("touchstart", move, { passive: true });
  plot.addEventListener("touchmove", move, { passive: true });
  return () => {
    plot.removeEventListener("touchstart", move);
    plot.removeEventListener("touchmove", move);
  };
}
