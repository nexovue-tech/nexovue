import { describe, expect, it } from "vitest";

import type { LayoutPanel } from "./layout-rows";
import { layoutRows } from "./layout-rows";

/**
 * F20 FR49, AC67: a panel's width is its share of a row, and the last panel in each row
 * grows to fill it, so no saved layout leaves empty columns on the right. From `lg` to
 * `xl` a chart or table of width 3 or 4 spans 6; from `xl` a table spans at least 4; below
 * `lg` every panel is full width.
 */
const chart = (id: string, width: number): LayoutPanel => ({ id, width, shape: "time" });
const table = (id: string, width: number): LayoutPanel => ({ id, width, shape: "table" });
const text = (id: string, width: number): LayoutPanel => ({ id, width, shape: "text" });

const spans = (rows: ReturnType<typeof layoutRows>) =>
  rows.map((row) => row.map((cell) => `${cell.id}:${cell.span}`));

describe("layoutRows", () => {
  it.each(["xl", "lg"] as const)("leaves no row short of 12 columns at %s", (size) => {
    const panels = [chart("a", 8), chart("b", 6), text("c", 3), chart("d", 12), text("e", 4)];
    for (const row of layoutRows(panels, size)) {
      expect(row.reduce((sum, cell) => sum + cell.span, 0)).toBe(12);
    }
  });

  it("packs widths into rows in order and grows the last of each", () => {
    const panels = [chart("a", 4), chart("b", 4), chart("c", 4), text("d", 3), text("e", 3)];
    expect(spans(layoutRows(panels, "xl"))).toEqual([
      ["a:4", "b:4", "c:4"],
      ["d:3", "e:9"],
    ]);
  });

  it("starts a row when a panel would not fit the one before it", () => {
    expect(spans(layoutRows([chart("a", 8), chart("b", 6)], "xl"))).toEqual([["a:12"], ["b:12"]]);
  });

  it("widens a narrow chart to 6 between lg and xl, and keeps text at its width", () => {
    const panels = [chart("a", 3), chart("b", 4), text("c", 3), text("d", 3)];
    expect(spans(layoutRows(panels, "lg"))).toEqual([
      ["a:6", "b:6"],
      ["c:3", "d:9"],
    ]);
  });

  it("widens a narrow table to 6 between lg and xl, and to at least 4 from xl", () => {
    expect(spans(layoutRows([table("t", 3)], "lg"))).toEqual([["t:12"]]);
    expect(layoutRows([table("t", 3), text("n", 3)], "lg")[0][0].span).toBe(6);
    expect(layoutRows([table("t", 3), text("n", 3)], "xl")[0][0].span).toBe(4);
  });

  it("makes every panel full width below lg", () => {
    expect(spans(layoutRows([chart("a", 3), text("b", 3)], "base"))).toEqual([["a:12"], ["b:12"]]);
  });

  it("draws nothing for no panels", () => {
    expect(layoutRows([], "xl")).toEqual([]);
  });
});
