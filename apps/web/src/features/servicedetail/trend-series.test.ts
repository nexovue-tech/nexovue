import { describe, expect, it } from "vitest";

import type { TrendPoint } from "./api";
import { buildTrendPlots, describePlot } from "./trend-series";

function point(overrides: Partial<TrendPoint> = {}): TrendPoint {
  return {
    bucket: "2026-08-30T10:00:00Z",
    requests_per_minute: 12,
    error_rate: 0.05,
    p50_ms: 12.4,
    p95_ms: 62.9,
    p99_ms: 188.0,
    ...overrides,
  };
}

const plotsOf = (points: TrendPoint[]) =>
  new Map(buildTrendPlots(points).map((p) => [p.metric, p]));

describe("buildTrendPlots", () => {
  it("draws one plot per unit, never one axis for three", () => {
    expect(buildTrendPlots([point()]).map((plot) => plot.metric)).toEqual([
      "rate",
      "errorRate",
      "latency",
    ]);
  });

  it("puts the x axis in seconds, which is what uPlot's time scale reads", () => {
    const [seconds] = buildTrendPlots([point()])[0].data;
    expect(seconds).toEqual([Date.parse("2026-08-30T10:00:00Z") / 1000]);
  });

  it("turns the wire's fraction into the percent the axis is read in", () => {
    expect(plotsOf([point({ error_rate: 0.055 })]).get("errorRate")?.data[1]).toEqual([5.5]);
  });

  it("carries latency through untouched — the API already reports milliseconds", () => {
    expect(plotsOf([point()]).get("latency")?.data[1]).toEqual([12.4]);
  });

  it("draws the three percentiles on one scale rather than three charts", () => {
    // The whole reason the panel exists. Three separately scaled charts each
    // fill their own box and look identical, so a p99 pulling away from a p95 —
    // a tail problem rather than a general slowdown — is invisible.
    const latency = plotsOf([point(), point({ bucket: "2026-08-30T10:01:00Z", p99_ms: 900 })]);
    const plot = latency.get("latency");

    expect(plot?.lineNames).toEqual(["p50", "p95", "p99"]);
    // One x row, then one row per line, in the same order as the names.
    expect(plot?.data).toHaveLength(4);
    expect(plot?.data[1]).toEqual([12.4, 12.4]);
    expect(plot?.data[2]).toEqual([62.9, 62.9]);
    expect(plot?.data[3]).toEqual([188.0, 900]);
  });

  it("keeps the other two panels to a single line", () => {
    // One unit, one line. A rate panel with three lines would be three
    // different measurements sharing an axis, which is the thing three separate
    // panels exist to prevent.
    const plots = plotsOf([point()]);

    expect(plots.get("rate")?.lineNames).toEqual(["Requests/min"]);
    expect(plots.get("errorRate")?.lineNames).toEqual(["Failure share"]);
  });

  it("calls a panel flat only when every line of it is zero", () => {
    // A service whose p50 is zero and whose tail is not has something to draw.
    const partlyZero = plotsOf([point({ p50_ms: 0, p95_ms: 0, p99_ms: 40 })]);
    const wholly = plotsOf([point({ p50_ms: 0, p95_ms: 0, p99_ms: 0 })]);

    expect(partlyZero.get("latency")?.flatAtZero).toBe(false);
    expect(wholly.get("latency")?.flatAtZero).toBe(true);
  });

  it("drops a bucket whose timestamp will not parse rather than plotting it at the epoch", () => {
    const plots = buildTrendPlots([point(), point({ bucket: "not a time" })]);

    for (const plot of plots) {
      expect(plot.data[0]).toHaveLength(1);
      expect(plot.data[1]).toHaveLength(1);
    }
  });

  it("marks a series that is zero everywhere, so it can be said instead of drawn", () => {
    const plots = plotsOf([point({ error_rate: 0 }), point({ error_rate: 0 })]);

    expect(plots.get("errorRate")?.flatAtZero).toBe(true);
    expect(plots.get("errorRate")?.zeroText).toBe("Nothing failed in this window.");
    expect(plots.get("rate")?.flatAtZero).toBe(false);
  });

  it("does not call an empty window flat — there is nothing to be flat about", () => {
    for (const plot of buildTrendPlots([])) {
      expect(plot.flatAtZero).toBe(false);
      expect(plot.data[0]).toEqual([]);
    }
  });
});

describe("describePlot", () => {
  it("names the metric, the service and the unit for a reader who cannot see the canvas", () => {
    const plot = buildTrendPlots([point()])[2];
    const described = describePlot(plot, "demo-storefront", 1);

    expect(described).toContain("Duration");
    expect(described).toContain("demo-storefront");
    expect(described).toContain("milliseconds");
  });

  it("says zero rather than describing a line that is not there", () => {
    const plot = plotsOf([point({ error_rate: 0 })]).get("errorRate");
    expect(plot && describePlot(plot, "demo-storefront", 1)).toContain("zero across every bucket");
  });

  it("says so when the window holds no buckets at all", () => {
    expect(describePlot(buildTrendPlots([])[0], "demo-storefront", 0)).toContain("no buckets");
  });
});
