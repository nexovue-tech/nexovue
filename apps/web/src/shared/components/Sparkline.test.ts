// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { UNKNOWN } from "@/shared/format/number";

import { Sparkline } from "./Sparkline";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

function render(props: Parameters<typeof Sparkline>[0]): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() => root.render(createElement(Sparkline, props)));
  return container;
}

/** The y coordinates the polyline was drawn at, in order. */
function drawnHeights(container: HTMLElement): number[] {
  const points = container.querySelector("polyline")?.getAttribute("points") ?? "";
  return points
    .split(" ")
    .filter(Boolean)
    .map((pair) => Number(pair.split(",")[1]));
}

const FLAT = [10, 10, 10, 10, 10, 10];
const RISING = [10, 20, 40, 70, 110, 160];

describe("Sparkline", () => {
  it("draws the same series differently at different scales", () => {
    const own = drawnHeights(render({ points: FLAT, max: 10, label: "flat" }));
    const shared = drawnHeights(render({ points: FLAT, max: 160, label: "flat" }));

    // Against its own maximum a flat line pins to the top of the box; against the
    // screen's it sits near the floor. That difference is the whole reason `max` is the
    // caller's: without it these two render identically and the column cannot be read
    // across rows.
    expect(own[0]).toBeLessThan(shared[0]);
  });

  it("keeps two rows comparable when they share a scale", () => {
    const max = 160;
    const flat = drawnHeights(render({ points: FLAT, max, label: "flat" }));
    const rising = drawnHeights(render({ points: RISING, max, label: "rising" }));

    // Lower y is higher on screen. The spiking series must end visibly above the flat
    // one, which is the claim a reader makes when scanning the column.
    expect(rising[rising.length - 1]).toBeLessThan(flat[flat.length - 1]);
    expect(new Set(flat).size).toBe(1);
  });

  it("renders the em dash rather than inventing a trend from too few readings", () => {
    const container = render({ points: [4, 6, 9], max: 10, label: "thin" });

    expect(container.querySelector("svg")).toBeNull();
    expect(container.textContent).toBe(UNKNOWN);
  });

  it("renders the em dash when there is no scale to draw against", () => {
    for (const max of [0, Number.NaN]) {
      const container = render({ points: RISING, max, label: "no scale" });
      expect(container.textContent).toBe(UNKNOWN);
    }
  });

  it("survives an empty series", () => {
    expect(() => render({ points: [], max: 10, label: "empty" })).not.toThrow();
  });

  it("puts an absent reading on the floor rather than breaking the line", () => {
    const heights = drawnHeights(
      render({ points: [10, 20, Number.NaN, 40, 50, 60], max: 60, label: "gap" }),
    );

    // Every point is drawn — a gap would read as "nothing happened", which is the
    // opposite of "we did not measure".
    expect(heights).toHaveLength(6);
    expect(heights.every(Number.isFinite)).toBe(true);
  });

  it("strokes with severity when it is summarising the error series", () => {
    const normal = render({ points: RISING, max: 160, label: "rate" });
    const failing = render({ points: RISING, max: 160, tone: "critical", label: "errors" });

    expect(normal.innerHTML).toContain("stroke-series-1");
    expect(failing.innerHTML).toContain("stroke-critical-solid");
  });

  it("names itself for a reader who cannot see the drawing", () => {
    const container = render({ points: RISING, max: 160, label: "checkout-api, last hour" });

    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe(
      "checkout-api, last hour",
    );
  });
});

describe("Sparkline absent readings", () => {
  /** How many separate strokes the drawing is made of. */
  const strokes = (container: HTMLElement) => container.querySelectorAll("polyline").length;

  it("floors an absent reading by default, because a rate really was near zero", () => {
    const container = render({ points: [10, 20, null, 40, 50, 60], max: 60, label: "rate" });

    expect(strokes(container)).toBe(1);
    expect(drawnHeights(container)).toHaveLength(6);
  });

  it("breaks the line when a reading means nothing was measured", () => {
    // A synthetic check that failed has no response time. Drawn at zero it would read as
    // "the site got instantly fast", which is the opposite of what happened.
    const container = render({
      points: [180, 175, 182, null, null, 178, 181],
      max: 200,
      absent: "gap",
      label: "duration",
    });

    expect(strokes(container)).toBe(2);
  });

  it("does not draw a stub for a single reading stranded between gaps", () => {
    const container = render({
      points: [180, 175, null, 190, null, 178, 181],
      max: 200,
      absent: "gap",
      label: "duration",
    });

    // The lone 190 has nothing to join to; a one-point polyline draws nothing anyway and
    // must not be emitted as an empty element.
    expect(strokes(container)).toBe(2);
  });

  it("omits the endpoint dot when the series ends in a gap", () => {
    const ending = render({
      points: [180, 175, 182, 178, null, null],
      max: 200,
      absent: "gap",
      label: "down now",
    });
    const running = render({
      points: [180, 175, 182, 178, 181, 179],
      max: 200,
      absent: "gap",
      label: "up now",
    });

    // No dot is itself the reading: the check is down right now.
    expect(ending.querySelectorAll("circle")).toHaveLength(0);
    expect(running.querySelectorAll("circle")).toHaveLength(1);
  });
});
