// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type { HeatmapColumn } from "./DurationHeatmap";
import { DurationHeatmap } from "./DurationHeatmap";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

const BOUNDS = [1, 5, 25];

const COLUMNS: HeatmapColumn[] = [
  { bucket: "2026-09-13T10:00:00Z", counts: [8, 1, 1, 0], total: 10 },
  { bucket: "2026-09-13T10:01:00Z", counts: [1, 1, 1, 7], total: 10 },
];

function render(props: Partial<Parameters<typeof DurationHeatmap>[0]> = {}): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() =>
    root.render(
      createElement(DurationHeatmap, {
        bounds: BOUNDS,
        columns: COLUMNS,
        unit: "ms",
        label: "checkout's latency",
        ...props,
      }),
    ),
  );
  return container;
}

const cells = (container: HTMLElement) => [...container.querySelectorAll("rect")];

describe("the grid", () => {
  it("draws one row per bucket, which is one more than there are bounds", () => {
    // The extra row is everything above the last boundary — the row a reader
    // looking for a tail goes to first.
    const container = render();

    expect(cells(container)).toHaveLength(COLUMNS.length * (BOUNDS.length + 1));
  });

  it("draws nothing at all rather than an empty grid when there is no histogram", () => {
    // OD7: absent, not empty. An empty heatmap and a service with no traffic
    // are the same picture, and only one of them is true.
    expect(cells(render({ columns: [] }))).toHaveLength(0);
    expect(cells(render({ bounds: [] }))).toHaveLength(0);
  });

  it("puts the fastest bucket at the bottom, so a tail grows upward", () => {
    const container = render();
    const first = cells(container).slice(0, BOUNDS.length + 1);

    const ys = first.map((cell) => Number(cell.getAttribute("y")));
    expect(ys).toEqual([...ys].sort((left, right) => right - left));
  });
});

describe("colour says shape, not volume", () => {
  it("scales each column to its own traffic", () => {
    // Scaled to the busiest cell in the grid instead, a quiet hour is black
    // whatever its shape — and "when was it busy" is what the rate chart above
    // already answers. Both columns here hold ten observations distributed
    // differently, and each one's dominant bucket must read as dominant.
    const container = render();
    const opacity = cells(container).map((cell) => Number(cell.getAttribute("opacity")));

    const firstColumn = opacity.slice(0, 4);
    const secondColumn = opacity.slice(4);
    expect(Math.max(...firstColumn)).toBe(firstColumn[0]);
    expect(Math.max(...secondColumn)).toBe(secondColumn[3]);
  });

  it("leaves an empty bucket invisible and a nearly-empty one visible", () => {
    // A bucket holding one observation in ten thousand still happened, and a
    // tail is exactly where that matters.
    const container = render({
      columns: [{ bucket: "2026-09-13T10:00:00Z", counts: [9_999, 0, 0, 1], total: 10_000 }],
    });
    const opacity = cells(container).map((cell) => Number(cell.getAttribute("opacity")));

    expect(opacity[1]).toBe(0);
    expect(opacity[3]).toBeGreaterThan(0);
  });

  it("does not divide by zero on a column that reported nothing", () => {
    const container = render({
      columns: [{ bucket: "2026-09-13T10:00:00Z", counts: [0, 0, 0, 0], total: 0 }],
    });

    expect(cells(container).every((cell) => Number(cell.getAttribute("opacity")) === 0)).toBe(true);
  });
});

describe("the reader is told what the rows mean", () => {
  it("labels the scale in the metric's own unit", () => {
    const container = render();

    expect(container.textContent).toContain("1ms");
    expect(container.textContent).toContain("25ms");
  });

  it("names the unbounded row as over the last bound", () => {
    const container = render();
    const titles = [...container.querySelectorAll("title")].map((node) => node.textContent ?? "");

    expect(titles.some((title) => title.startsWith("over 25ms"))).toBe(true);
  });
});
