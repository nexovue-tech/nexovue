// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import type uPlot from "uplot";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DeployMarker } from "../deploy-markers";
import type { PlotRows } from "./TrendChart";
import { ONE_READING_SPAN_SECONDS } from "./trend-chart-options";

/**
 * The real uPlot, with a canvas that draws nothing (jsdom has none), to pin the order of
 * a chart's first moments. uPlot works its scales out in a microtask after it is built; a
 * `redraw()` that lands before then used to throw the x scale away (open decision 39).
 */

const drawn: uPlot[] = [];

vi.mock("uplot", async (importOriginal) => {
  const real = await importOriginal<{ default: typeof uPlot }>();
  const Real = real.default as unknown as new (...args: unknown[]) => uPlot;
  // uPlot's constructor returns its own object, so the subclass only records it.
  class Recorded extends Real {
    constructor(...args: unknown[]) {
      super(...args);
      drawn.push(this);
    }
  }
  return { default: Object.assign(Recorded, real.default) };
});

const { TrendChart } = await import("./TrendChart");

const MINUTE = 60;
const FIRST_BUCKET = 1_795_338_000;
const DATA: PlotRows = [
  [0, 1, 2, 3].map((index) => FIRST_BUCKET + index * MINUTE),
  [10, 20, 30, 40],
];

/** A 2D context that accepts every call and measures every text as ten pixels wide. */
function blankContext(): CanvasRenderingContext2D {
  return new Proxy({} as CanvasRenderingContext2D, {
    get: (target, name) =>
      name === "measureText"
        ? () => ({ width: 10 })
        : (Reflect.get(target, name) ?? (() => undefined)),
    set: (target, name, value) => Reflect.set(target, name, value),
  });
}

const mark = (at: number): DeployMarker => ({
  at,
  version: "v2",
  service: "checkout",
  source: "reported",
  state: "finished",
  releaseId: "release-1",
  key: `mark-${at}`,
});

function chart(markers: readonly DeployMarker[], data: PlotRows = DATA) {
  return createElement(TrendChart, {
    label: "Requests/min",
    seriesToken: 1,
    data,
    formatValue: String,
    description: "Requests per minute",
    markers,
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((() =>
    blankContext()) as unknown as typeof HTMLCanvasElement.prototype.getContext);
});

afterEach(() => {
  vi.restoreAllMocks();
  drawn.length = 0;
  document.body.replaceChildren();
});

describe("a chart's first moments", () => {
  it("keeps its x scale when a mark arrives before uPlot has first drawn", async () => {
    const container = document.body.appendChild(document.createElement("div"));
    const root = createRoot(container);

    // Two renders with no wait between them: no microtask runs, so uPlot has not yet
    // worked its scales out when the new marks ask for a redraw.
    act(() => root.render(chart([])));
    act(() => root.render(chart([mark(FIRST_BUCKET + MINUTE)])));
    await act(async () => undefined);

    const [plot] = drawn;
    expect(plot?.scales.x?.min).toBe(DATA[0][0]);
    expect(plot?.scales.x?.max).toBe(DATA[0].at(-1));
    act(() => root.unmount());
  });

  it("keeps its x scale when marks arrive after it has drawn", async () => {
    const container = document.body.appendChild(document.createElement("div"));
    const root = createRoot(container);

    await act(async () => root.render(chart([])));
    await act(async () => root.render(chart([mark(FIRST_BUCKET + MINUTE)])));

    const [plot] = drawn;
    expect(plot?.scales.x?.min).toBe(DATA[0][0]);
    expect(plot?.scales.x?.max).toBe(DATA[0].at(-1));
    act(() => root.unmount());
  });

  it("gives a single reading a span around it, not uPlot's 2.7 years", async () => {
    const container = document.body.appendChild(document.createElement("div"));
    const root = createRoot(container);
    const only: PlotRows = [[FIRST_BUCKET], [10]];

    await act(async () => root.render(chart([], only)));

    const [plot] = drawn;
    const min = plot?.scales.x?.min ?? Number.NaN;
    const max = plot?.scales.x?.max ?? Number.NaN;
    expect(min).toBeLessThan(FIRST_BUCKET);
    expect(max).toBeGreaterThan(FIRST_BUCKET);
    expect(max - min).toBe(ONE_READING_SPAN_SECONDS);
    expect((min + max) / 2).toBe(FIRST_BUCKET);
    act(() => root.unmount());
  });
});
