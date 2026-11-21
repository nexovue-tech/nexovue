// @vitest-environment jsdom

import type uPlot from "uplot";
import { describe, expect, it } from "vitest";

import type { ChartPalette } from "./trend-chart-options";
import { chartOptions } from "./trend-chart-options";

/**
 * `TrendChart`'s uPlot options as plain data, since jsdom has no canvas (F20, T4.3, T4.4).
 *
 * The snapshot was recorded from `chartOptions` moved out of `TrendChart.tsx` unchanged,
 * before any new prop existed (T4.3). Every screen that draws a `TrendChart` passes none
 * of the new props, so its options must still equal that snapshot (AC43's data half,
 * NFR7). Each new prop then has its own case, which must differ from it.
 */

const PALETTE: ChartPalette = {
  line: "probed-line",
  axis: "probed-axis",
  grid: "probed-grid",
  font: "11px mono",
  series: [
    "probed-series-1",
    "probed-series-2",
    "probed-series-3",
    "probed-series-4",
    "probed-series-5",
    "probed-series-6",
  ],
};
const WIDTH = 480;
const NOTHING = () => [];
const NO_BAND = () => undefined;
const formatMs = (value: number) => `${value} ms`;

/** Functions replaced by their name, and the ones that compute something, computed. */
function asData(options: uPlot.Options): unknown {
  const plain = JSON.parse(
    JSON.stringify(options, (_key, value) =>
      typeof value === "function" ? `ƒ ${value.name || "anonymous"}` : value,
    ),
  );
  const yRange = options.scales?.y?.range;
  const yValues = options.axes?.[1]?.values;
  const chart = {} as uPlot;
  return {
    plain,
    yRange:
      typeof yRange === "function"
        ? [
            [0, 0],
            [0, 10],
            [2, 50],
            [-4, 8],
          ].map(([min, max]) => yRange(chart, min ?? 0, max ?? 0, "y"))
        : yRange,
    yValues: typeof yValues === "function" ? yValues(chart, [0, 1500], 1, 10, 0) : yValues,
    hooks: Object.fromEntries(
      Object.entries(options.hooks ?? {}).map(([name, list]) => [name, list?.length ?? 0]),
    ),
    tzDate: options.tzDate ? options.tzDate(0).getTime() : null,
  };
}

/** Today's two shapes of call: one line, and three on one scale with every option on. */
function todays(): Record<string, uPlot.Options> {
  return {
    oneLine: chartOptions(PALETTE, WIDTH, formatMs, ["Requests/min"], NOTHING, NOTHING, NO_BAND),
    threeLines: chartOptions(
      PALETTE,
      WIDTH,
      formatMs,
      ["p50", "p95", "p99"],
      NOTHING,
      NOTHING,
      NO_BAND,
      () => {},
      true,
      "Asia/Kolkata",
      () => {},
    ),
  };
}

describe("TrendChart's options with no new prop", () => {
  it("equal the snapshot recorded before any new prop existed (AC43)", () => {
    for (const [name, options] of Object.entries(todays())) {
      expect(asData(options)).toMatchSnapshot(name);
    }
  });

  it("are what an empty set of new props gives, so a screen passing none is unchanged", () => {
    const [plainLine, emptyLine] = [
      chartOptions(PALETTE, WIDTH, formatMs, ["Requests/min"], NOTHING, NOTHING, NO_BAND),
      chartOptions(
        PALETTE,
        WIDTH,
        formatMs,
        ["Requests/min"],
        NOTHING,
        NOTHING,
        NO_BAND,
        undefined,
        false,
        undefined,
        undefined,
        {},
      ),
    ];
    expect(asData(emptyLine)).toEqual(asData(plainLine));
  });
});

describe("each new prop changes the options, and only as it says", () => {
  const base = () =>
    asData(
      chartOptions(PALETTE, WIDTH, formatMs, ["a", "b", "c", "d", "e"], NOTHING, NOTHING, NO_BAND),
    ) as { plain: Record<string, unknown>; yRange: unknown };
  const withExtras = (extras: Parameters<typeof chartOptions>[11]) =>
    chartOptions(
      PALETTE,
      WIDTH,
      formatMs,
      ["a", "b", "c", "d", "e"],
      NOTHING,
      NOTHING,
      NO_BAND,
      undefined,
      false,
      undefined,
      undefined,
      extras,
    );

  it("height sets the chart's height", () => {
    const options = withExtras({ height: 240 });
    expect(options.height).toBe(240);
    expect(asData(options)).not.toEqual(base());
  });

  it("a fitted y range leaves zero off and keeps headroom both ways", () => {
    const range = withExtras({ yRange: "fit" }).scales?.y?.range;
    expect(typeof range).toBe("function");
    if (typeof range !== "function") return;
    const [low, high] = range({} as uPlot, 40, 60, "y");
    expect(low).toBeGreaterThan(0);
    expect(low).toBeLessThan(40);
    expect(high).toBeGreaterThan(60);
    // A flat line still gets a scale.
    const [flatLow, flatHigh] = range({} as uPlot, 0.5, 0.5, "y");
    expect(flatHigh).toBeGreaterThan(flatLow ?? 0);
  });

  it("distinct colours give each line its own series colour and its own dash", () => {
    const series = withExtras({ distinctColours: true }).series.slice(1);
    expect(series.map((line) => line.stroke)).toEqual(PALETTE.series?.slice(0, 5));
    const dashes = series.map((line) => JSON.stringify(line.dash ?? null));
    expect(new Set(dashes).size).toBe(5);
  });

  it("a readout turns uPlot's own legend off, shows cursor points and formats every value", () => {
    const seen: (number | null)[] = [];
    const options = withExtras({ readout: (index) => seen.push(index) });
    expect(options.legend?.show).toBe(false);
    expect(options.cursor?.points?.show).toBe(true);
    for (const line of options.series.slice(1)) {
      expect(typeof line.value).toBe("function");
      if (typeof line.value === "function") {
        expect(line.value({} as uPlot, 12, 1, 0)).toBe("12 ms");
        expect(line.value({} as uPlot, null as unknown as number, 1, 0)).toBe("—");
      }
    }
    const [onCursor] = options.hooks?.setCursor ?? [];
    onCursor?.({ cursor: { idx: 7 } } as uPlot);
    onCursor?.({ cursor: { idx: null } } as unknown as uPlot);
    expect(seen).toEqual([7, null]);
  });

  it("a sync key links cursors and never passes a press or a release on (AC31's data half)", () => {
    const sync = withExtras({ syncKey: "dashboard-1" }).cursor?.sync;
    expect(sync?.key).toBe("dashboard-1");
    const publish = sync?.filters?.pub;
    expect(publish).toBeDefined();
    const pass = (type: string) =>
      publish?.(type as Parameters<NonNullable<typeof publish>>[0], {} as uPlot, 0, 0, 0, 0, 0);
    expect(pass("mousemove")).toBe(true);
    expect(pass("mouseleave")).toBe(true);
    expect(pass("mousedown")).toBe(false);
    expect(pass("mouseup")).toBe(false);
  });
});

describe("release rules", () => {
  function dashesDrawn(dottedSeen: boolean | undefined): number[][] {
    const dashes: number[][] = [];
    const context = {
      save() {},
      restore() {},
      beginPath() {},
      rect() {},
      clip() {},
      moveTo() {},
      lineTo() {},
      stroke() {},
      fillRect() {},
      setLineDash(dash: number[]) {
        dashes.push(dash);
      },
    };
    const markers = [
      { at: 10, version: "v2", service: "checkout", source: "seen" as const },
      { at: 20, version: "v3", service: "checkout", source: "reported" as const },
    ].map((marker) => ({ ...marker, state: null, releaseId: null, key: marker.version }));
    const options = chartOptions(
      PALETTE,
      WIDTH,
      formatMs,
      ["Requests/min"],
      () => markers,
      NOTHING,
      NO_BAND,
      undefined,
      false,
      undefined,
      undefined,
      dottedSeen === undefined ? {} : { dottedSeen },
    );
    const chart = {
      ctx: context,
      bbox: { left: 0, top: 0, width: 100, height: 50 },
      valToPos: () => 5,
    } as unknown as uPlot;
    for (const hook of options.hooks?.drawClear ?? []) hook?.(chart);
    return dashes;
  }

  it("are all dashed, as today, unless asked", () => {
    expect(dashesDrawn(undefined)).toEqual([
      [3, 3],
      [3, 3],
    ]);
  });

  it("draw a version seen in telemetry dotted and a reported deploy dashed (D14)", () => {
    expect(dashesDrawn(true)).toEqual([
      [1, 3],
      [3, 3],
    ]);
  });
});
