import { describe, expect, it } from "vitest";

import { bandStretches } from "@/shared/components/chart-band";

import type { FindingSignal, SignalSeries } from "./api";
import {
  bandSentence,
  evidenceSentence,
  ownEndpoint,
  PLOT_LINES,
  signalPlot,
} from "./finding-page";
import { finding } from "./render-harness.test-util";

/**
 * What the finding page draws and offers, apart from how (FR31). AC38: each signal's
 * readings are drawn against its normal band, from the normal to the edge the
 * sensitivity's minimum change sets.
 */

const AT = ["2026-11-01T10:00:00Z", "2026-11-01T10:01:00Z", "2026-11-01T10:02:00Z"];
const seconds = AT.map((at) => Date.parse(at) / 1000);

const slower: SignalSeries = {
  signal: "slower",
  unit: "ms",
  points: [
    { at: AT[0] ?? "", value: 112, normal: 110, edge: 165 },
    { at: AT[1] ?? "", value: 330, normal: 120, edge: 180 },
    { at: AT[2] ?? "", value: null, normal: null, edge: null },
  ],
};

const traffic: SignalSeries = {
  signal: "traffic",
  unit: "rpm",
  points: [
    { at: AT[0] ?? "", value: 400, normal: 1000, edge: 500 },
    { at: AT[1] ?? "", value: 300, normal: 1000, edge: 500 },
  ],
};

describe("a signal's chart (AC38)", () => {
  it("plots the readings, the normal and the band's edge on one scale", () => {
    const plot = signalPlot(slower);

    expect(PLOT_LINES).toEqual(["This finding", "Normal", "Edge of the normal band"]);
    expect(plot.data).toEqual([seconds, [112, 330, null], [110, 120, null], [165, 180, null]]);
  });

  it("fills the band from the normal up to the edge, where slower means higher", () => {
    const plot = signalPlot(slower);

    expect(plot.band).toEqual({ upper: 3, lower: 2 });
    expect(bandStretches(plot.data, plot.band)).toEqual([
      { xs: seconds.slice(0, 2), upper: [165, 180], lower: [110, 120] },
    ]);
  });

  it("fills it from the edge up to the normal for losing traffic, which is judged below it", () => {
    const plot = signalPlot(traffic);

    expect(plot.band).toEqual({ upper: 2, lower: 3 });
    const [stretch] = bandStretches(plot.data, plot.band);
    expect(stretch?.upper).toEqual([1000, 1000]);
    expect(stretch?.lower).toEqual([500, 500]);
  });

  it("draws no band at all for a finding whose readings have expired (AC40)", () => {
    const plot = signalPlot({ ...slower, points: [] });

    expect(bandStretches(plot.data, plot.band)).toEqual([]);
  });

  it("is the series the page draws for its finding", () => {
    const [series] = finding().series;
    if (!series) throw new Error("the fixture has a series");

    expect(bandStretches(signalPlot(series).data, signalPlot(series).band)).toHaveLength(1);
  });
});

const stored: FindingSignal = {
  signal: "slower",
  key: "slower",
  normal: 110,
  band_low: 110,
  band_high: 165,
  current: 330,
  worst: 412,
  unit: "ms",
  comparison: "Tuesdays around 15:00, last 3 weeks",
  endpoint: "POST /pay",
  endpoint_current: 400,
  endpoint_last_week: 100,
};

describe("a signal's evidence endpoint (FR3, FR31)", () => {
  it("names a slower endpoint's p95 against the same time last week", () => {
    expect(evidenceSentence(stored)).toBe(
      "POST /pay: p95 400ms, against 100ms the same time last week.",
    );
  });

  it("names a failing endpoint's share failing against the same time last week", () => {
    const failing: FindingSignal = {
      ...stored,
      signal: "failing",
      unit: "%",
      endpoint_current: 12.5,
      endpoint_last_week: 0,
    };

    expect(evidenceSentence(failing)).toBe(
      "POST /pay: 12.5% failing, against 0.0% the same time last week.",
    );
  });

  it("writes a value it does not have as the em dash, never a zero", () => {
    const older = { ...stored, endpoint_current: null, endpoint_last_week: null };

    expect(evidenceSentence(older)).toBe("POST /pay: p95 —, against — the same time last week.");
  });

  it("says nothing for a signal that names no endpoint", () => {
    expect(evidenceSentence({ ...stored, endpoint: null })).toBeNull();
  });
});

describe("a signal's stored band (FR16)", () => {
  it("reads from the normal to its edge, in the signal's unit", () => {
    expect(bandSentence(stored)).toBe("Normal band 110ms to 165ms.");
  });

  it("is the em dash for a finding stored before its band was kept", () => {
    expect(bandSentence({ ...stored, band_low: null, band_high: null })).toBe("Normal band —.");
  });
});

describe("ownEndpoint", () => {
  const base = finding().signals[0] as FindingSignal;

  it("names the endpoint only on an endpoint's own entry, not on a service's evidence", () => {
    expect(
      ownEndpoint({ ...base, signal: "failing", key: "endpoint:POST /pay", endpoint: "POST /pay" }),
    ).toBe("POST /pay");
    expect(
      ownEndpoint({ ...base, signal: "failing", key: "failing", endpoint: "GET /x" }),
    ).toBeNull();
    expect(
      ownEndpoint({ ...base, signal: "slower", key: "slower", endpoint: "GET /x" }),
    ).toBeNull();
  });

  it("keeps an entry stored before keys were kept as it was", () => {
    expect(ownEndpoint({ ...base, key: null, endpoint: "GET /x" })).toBe("GET /x");
  });
});
