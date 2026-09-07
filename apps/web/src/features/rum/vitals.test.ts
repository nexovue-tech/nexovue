import { describe, expect, it } from "vitest";

import type { WebVitalSummary } from "./api";
import {
  CLS,
  describeReadings,
  describeVitals,
  formatValue,
  INP,
  LCP,
  MIN_READINGS,
  rate,
  readableGroup,
  readableVitals,
  TTFB,
  UNLABELLED_GROUP,
  UNMEASURED,
} from "./vitals";

const enough = MIN_READINGS;

function summary(name: string, p75: number, readings = enough): WebVitalSummary {
  return { name, p75, readings };
}

describe("rating a percentile against its own thresholds", () => {
  // The boundaries are web-vitals@6.2.1's own. A value exactly on the ceiling is good;
  // the library treats the threshold as inclusive and so must this.
  it.each([
    [LCP, 2500, "good"],
    [LCP, 2500.1, "needs-improvement"],
    [LCP, 4000, "needs-improvement"],
    [LCP, 4000.1, "poor"],
    [INP, 200, "good"],
    [INP, 500.1, "poor"],
    [CLS, 0.1, "good"],
    [CLS, 0.25, "needs-improvement"],
    [CLS, 0.26, "poor"],
    [TTFB, 800, "good"],
  ])("%s at %s rates %s", (name, value, expected) => {
    expect(rate(name, value, enough)).toBe(expected);
  });

  it("refuses to rate a percentile drawn from too few readings", () => {
    // The number would render identically to a trustworthy one, which is the whole
    // reason migration 000031 stores reading_count.
    expect(rate(LCP, 900, MIN_READINGS - 1)).toBe("insufficient");
    expect(rate(LCP, 900, MIN_READINGS)).toBe("good");
  });

  it("does not rate a vital it has no thresholds for", () => {
    expect(rate("fid", 10, enough)).toBe("insufficient");
  });
});

describe("formatting a value a person has to read", () => {
  it("gives a duration past a second in seconds", () => {
    expect(formatValue(LCP, 2412)).toBe("2.41 s");
    expect(formatValue(LCP, 1000)).toBe("1.00 s");
  });

  it("keeps a sub-second duration in milliseconds and whole", () => {
    expect(formatValue(INP, 184.6)).toBe("185 ms");
  });

  it("writes CLS with no unit at all, because it has none", () => {
    expect(formatValue(CLS, 0.0834)).toBe("0.08");
  });
});

describe("the five cards", () => {
  it("always returns all five, in a fixed order, reported or not", () => {
    const vitals = readableVitals([summary(LCP, 2000)]);
    expect(vitals.map((vital) => vital.name)).toEqual(["lcp", "inp", "cls", "fcp", "ttfb"]);
  });

  it("shows an em dash and no readings for a vital nobody reported", () => {
    // CLS is never reported outside Chromium, so absence is a normal state.
    const cls = readableVitals([summary(LCP, 2000)]).find((vital) => vital.name === CLS);
    expect(cls?.display).toBe("—");
    expect(cls?.readings).toBe(0);
    expect(cls?.rating).toBe("insufficient");
  });

  it("marks the three Core Web Vitals apart from the two diagnostics", () => {
    const vitals = readableVitals([]);
    expect(vitals.filter((vital) => vital.core).map((vital) => vital.label)).toEqual([
      "LCP",
      "INP",
      "CLS",
    ]);
  });
});

describe("a breakdown row", () => {
  it("keeps the same fixed column order as the cards", () => {
    const group = readableGroup({
      key: "/",
      is_route_template: false,
      vitals: [summary(LCP, 2000)],
    });
    expect(group.cells.map((cell) => cell.name)).toEqual(["lcp", "inp", "cls", "fcp", "ttfb"]);
  });

  it("shows a dash instead of a percentile the split made too thin", () => {
    // The whole reason the threshold matters more here: a page seen three times
    // has three readings, and its p75 is one visitor's morning.
    const group = readableGroup({
      key: "/rare",
      is_route_template: false,
      vitals: [summary(LCP, 120, MIN_READINGS - 1)],
    });
    const lcp = group.cells.find((cell) => cell.name === LCP);
    expect(lcp?.display).toBe(UNMEASURED);
    expect(lcp?.detail).toContain("too few for a percentile");
  });

  it("shows the value once the split still leaves enough behind it", () => {
    const group = readableGroup({
      key: "/busy",
      is_route_template: false,
      vitals: [summary(LCP, 2412, MIN_READINGS)],
    });
    const lcp = group.cells.find((cell) => cell.name === LCP);
    expect(lcp?.display).toBe("2.41 s");
    expect(lcp?.rating).toBe("good");
  });

  it("separates a vital nobody reported here from one reported too rarely", () => {
    expect(describeReadings(0)).toContain("not reported");
    expect(describeReadings(1)).toContain("too few");
    expect(describeReadings(MIN_READINGS)).toBe(`${MIN_READINGS} readings`);
  });

  it("carries whether its key is a route template or one URL", () => {
    // They mean different things to the person reading: a template is "this
    // page", a path is "this URL". The screen has to be able to say which.
    const routed = readableGroup({
      key: "/cart/checkout/{orderId}",
      is_route_template: true,
      vitals: [],
    });
    const raw = readableGroup({
      key: "/cart/checkout/978ac453-aa9f-11f1-91c3-1e31e47ab720",
      is_route_template: false,
      vitals: [],
    });
    expect(routed.isRouteTemplate).toBe(true);
    expect(raw.isRouteTemplate).toBe(false);
    // The label is the key either way; nothing is rewritten for display.
    expect(routed.label).toBe("/cart/checkout/{orderId}");
  });

  it("names an empty key rather than rendering a blank row", () => {
    // A device class the browser never sent is a real group, not a missing one.
    expect(readableGroup({ key: "", is_route_template: false, vitals: [] }).label).toBe(
      UNLABELLED_GROUP,
    );
    expect(readableGroup({ key: "mobile", is_route_template: false, vitals: [] }).label).toBe(
      "mobile",
    );
  });
});

describe("the sentence under the heading", () => {
  it("says nothing arrived when nothing did", () => {
    expect(describeVitals(readableVitals([]), "hour")).toContain("No browser reported");
  });

  it("says the readings are too few rather than describing a percentile", () => {
    const thin = readableVitals([summary(LCP, 9000, 3)]);
    const sentence = describeVitals(thin, "hour");
    expect(sentence).toContain("too few");
    // The number itself must not appear: it would be read as a measurement.
    expect(sentence).not.toContain("9");
  });

  it("names the worst Core Web Vital rather than summarising all five", () => {
    const vitals = readableVitals([
      summary(LCP, 5000), // poor
      summary(INP, 300), // needs improvement
      summary(TTFB, 3000), // poor, but a diagnostic
    ]);
    const sentence = describeVitals(vitals, "24 hours");
    expect(sentence).toContain("LCP");
    expect(sentence).toContain("poor");
    expect(sentence).not.toContain("TTFB");
  });

  it("prefers a poor core vital over one that merely needs improvement", () => {
    const vitals = readableVitals([summary(INP, 300), summary(CLS, 0.4)]);
    expect(describeVitals(vitals, "hour")).toContain("CLS");
  });

  it("says so plainly when everything measured is good", () => {
    const vitals = readableVitals([summary(LCP, 1200), summary(INP, 90), summary(CLS, 0.02)]);
    expect(describeVitals(vitals, "hour")).toContain("good");
  });
});
