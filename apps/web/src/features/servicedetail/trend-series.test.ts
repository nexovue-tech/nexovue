import { describe, expect, it } from "vitest";

import type { TrendPoint } from "./api";
import { buildTrendPlots, describePlot } from "./trend-series";

function point(overrides: Partial<TrendPoint> = {}): TrendPoint {
  return {
    bucket: "2026-08-30T10:00:00Z",
    requests_per_minute: 12,
    error_rate: 0.05,
    p95_ms: 62.9,
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
    expect(plotsOf([point()]).get("latency")?.data[1]).toEqual([62.9]);
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

    expect(described).toContain("p95 latency");
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
