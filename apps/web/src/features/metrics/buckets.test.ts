import { describe, expect, it } from "vitest";

import type { HistogramLayout } from "./api";
import { formatBound, histogramBars, meanOf } from "./buckets";

const LAYOUT: HistogramLayout = {
  bounds: [1, 5, 25],
  bucket_counts: [2, 8, 40, 1],
  count: 51,
  sum: 510,
  min: 0.2,
  max: 90,
  services: ["checkout-api"],
  series: 2,
  p50: 12,
  p75: 20,
  p90: 24,
  p95: 25,
  p99: 25,
};

describe("reading a bound in the metric's own unit", () => {
  it("renders seconds as a duration rather than as a bare number", () => {
    // The exporter's unit is not guessed at: `0.25` in seconds is 250ms, and
    // printing "0.25" would leave a reader to work that out.
    expect(formatBound(0.25, "s")).toBe(formatBound(250, "ms"));
  });

  it("renders a size as a size", () => {
    expect(formatBound(1_000, "By")).toContain("kB");
  });

  it("keeps an unfamiliar unit beside the number instead of inventing a scale", () => {
    // Relabelling somebody's data is worse than printing it plainly.
    expect(formatBound(42, "requests")).toBe("42 requests");
  });

  it("renders an absent reading as an em dash and never as zero", () => {
    expect(formatBound(null, "ms")).toBe("—");
    expect(formatBound(undefined, "ms")).toBe("—");
  });
});

describe("turning buckets into bars", () => {
  it("gives one bar per bucket, which is one more than there are bounds", () => {
    const bars = histogramBars(LAYOUT, "ms");

    expect(bars).toHaveLength(LAYOUT.bounds.length + 1);
  });

  it("scales to the tallest bucket, not to the total", () => {
    // A distribution where most traffic sits in one bucket is the normal case.
    // Scaling to the total would draw every other bucket as nothing at all.
    const bars = histogramBars(LAYOUT, "ms");

    expect(bars[2].height).toBe(1);
    expect(bars[1].height).toBeCloseTo(8 / 40);
  });

  it("names the open-ended bucket as over the last bound, not as a range", () => {
    const bars = histogramBars(LAYOUT, "ms");

    expect(bars.at(-1)?.unbounded).toBe(true);
    expect(bars.at(-1)?.label).toContain("over");
    expect(bars[0].label).toContain("up to");
  });

  it("survives a histogram with no bounds at all", () => {
    // A single unbounded bucket is legal OTLP and is in the store.
    const bars = histogramBars({ ...LAYOUT, bounds: [], bucket_counts: [7] }, "ms");

    expect(bars).toHaveLength(1);
    expect(bars[0].unbounded).toBe(true);
  });

  it("does not divide by zero when every bucket is empty", () => {
    const bars = histogramBars({ ...LAYOUT, bucket_counts: [0, 0, 0, 0], count: 0 }, "ms");

    expect(bars.every((bar) => bar.height === 0)).toBe(true);
  });
});

describe("the mean", () => {
  it("comes from the exporter's own sum", () => {
    expect(meanOf(LAYOUT)).toBeCloseTo(10);
  });

  it("is absent rather than guessed when no sum was sent", () => {
    // Deriving it from bucket midpoints would present a guess with the same
    // weight as a measurement.
    expect(meanOf({ ...LAYOUT, sum: null })).toBeNull();
    expect(meanOf({ ...LAYOUT, count: 0 })).toBeNull();
  });
});
