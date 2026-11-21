import type { StoredPanel } from "./api";
import { layoutRows } from "./layout-rows";
import { TABLE_KINDS, TIME_KINDS } from "./panel-words";

/**
 * Each panel's grid classes at `@3xl` and at `@5xl`, from `layoutRows` (FR49), so the read
 * view and the builder draw one layout: the builder shows the real result. Below `@3xl`
 * every panel is full width (`col-span-12`, set by the cell itself). The sizes are of
 * `main`, a container, not of the window: chat's panel narrows the screen (F4 T2.10b).
 */

const GRID_SIZES = ["@3xl", "@5xl"] as const;
// Literal classes, so Tailwind generates each one.
const SPAN_CLASS: Record<(typeof GRID_SIZES)[number], Record<number, string>> = {
  "@3xl": {
    1: "@3xl:col-span-1",
    2: "@3xl:col-span-2",
    3: "@3xl:col-span-3",
    4: "@3xl:col-span-4",
    5: "@3xl:col-span-5",
    6: "@3xl:col-span-6",
    7: "@3xl:col-span-7",
    8: "@3xl:col-span-8",
    9: "@3xl:col-span-9",
    10: "@3xl:col-span-10",
    11: "@3xl:col-span-11",
    12: "@3xl:col-span-12",
  },
  "@5xl": {
    1: "@5xl:col-span-1",
    2: "@5xl:col-span-2",
    3: "@5xl:col-span-3",
    4: "@5xl:col-span-4",
    5: "@5xl:col-span-5",
    6: "@5xl:col-span-6",
    7: "@5xl:col-span-7",
    8: "@5xl:col-span-8",
    9: "@5xl:col-span-9",
    10: "@5xl:col-span-10",
    11: "@5xl:col-span-11",
    12: "@5xl:col-span-12",
  },
};

export function gridSpans(panels: readonly StoredPanel[]): Record<string, string> {
  const shaped = panels.map((panel) => ({
    id: panel.id,
    width: panel.width,
    shape: TIME_KINDS.has(panel.kind)
      ? ("time" as const)
      : TABLE_KINDS.has(panel.kind)
        ? ("table" as const)
        : ("text" as const),
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
