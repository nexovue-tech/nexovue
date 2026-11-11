import type { StoredPanel } from "./api";
import { layoutRows } from "./layout-rows";
import { TIME_KINDS } from "./panel-words";

/**
 * Each panel's grid classes at `lg` and at `xl`, from `layoutRows` (FR49), so the read view
 * and the builder draw one layout: the builder shows the real result. Below `lg` every
 * panel is full width (`col-span-12`, set by the cell itself).
 */

const GRID_SIZES = ["lg", "xl"] as const;
// Literal classes, so Tailwind generates each one.
const SPAN_CLASS: Record<(typeof GRID_SIZES)[number], Record<number, string>> = {
  lg: {
    1: "lg:col-span-1",
    2: "lg:col-span-2",
    3: "lg:col-span-3",
    4: "lg:col-span-4",
    5: "lg:col-span-5",
    6: "lg:col-span-6",
    7: "lg:col-span-7",
    8: "lg:col-span-8",
    9: "lg:col-span-9",
    10: "lg:col-span-10",
    11: "lg:col-span-11",
    12: "lg:col-span-12",
  },
  xl: {
    1: "xl:col-span-1",
    2: "xl:col-span-2",
    3: "xl:col-span-3",
    4: "xl:col-span-4",
    5: "xl:col-span-5",
    6: "xl:col-span-6",
    7: "xl:col-span-7",
    8: "xl:col-span-8",
    9: "xl:col-span-9",
    10: "xl:col-span-10",
    11: "xl:col-span-11",
    12: "xl:col-span-12",
  },
};

export function gridSpans(panels: readonly StoredPanel[]): Record<string, string> {
  const shaped = panels.map((panel) => ({
    id: panel.id,
    width: panel.width,
    shape: TIME_KINDS.has(panel.kind) ? ("time" as const) : ("text" as const),
  }));
  const classes: Record<string, string[]> = {};
  for (const size of GRID_SIZES) {
    for (const row of layoutRows(shaped, size)) {
      for (const cell of row) {
        classes[cell.id] = [...(classes[cell.id] ?? []), SPAN_CLASS[size][cell.span]];
      }
    }
  }
  return Object.fromEntries(Object.entries(classes).map(([id, names]) => [id, names.join(" ")]));
}
