import { describe, expect, it } from "vitest";

import { clampSideways } from "./PanelDrag";

/**
 * The guard against the page scrolling sideways (T10.1, drag trial condition 3): a dragged
 * panel's edges stay inside the grid's, whatever the pointer does.
 */

const GRID = { left: 100, right: 1300 };
const PANEL = { left: 400, right: 800 };

describe("a dragged panel stays inside the grid sideways", () => {
  it("lets it move freely while it is inside", () => {
    expect(clampSideways(0, PANEL, GRID)).toBe(0);
    expect(clampSideways(-300, PANEL, GRID)).toBe(-300);
    expect(clampSideways(500, PANEL, GRID)).toBe(500);
  });

  it("stops it at the grid's left and right edges", () => {
    expect(clampSideways(-900, PANEL, GRID)).toBe(-300);
    expect(clampSideways(900, PANEL, GRID)).toBe(500);
  });

  it("keeps a panel wider than the grid where it was, and never throws", () => {
    expect(clampSideways(250, { left: 50, right: 1400 }, GRID)).toBe(0);
  });
});
