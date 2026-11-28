/**
 * Rows of a dashboard's grid, worked out in the client (F20, FR49, NFR6).
 *
 * A panel's `width` is its share of a 12-column row, and the last panel in each row grows
 * to fill it, so a saved layout never leaves empty columns on the right (the founder's
 * standing rule). Narrow charts and tables widen where the screen is narrow: at 768 px of
 * `main` a chart of width 3 would have about 100 px of plot, and a table scrolls rather than
 * squeezes (design, *Dashboard view*).
 */

export const COLUMNS = 12;
/** Between `@3xl` and `@5xl`, a chart or table of width 3 or 4 spans this many. */
const NARROW_SPAN = 6;
const NARROW_WIDTHS = new Set([3, 4]);
/** From `@5xl`, a table spans at least this many. */
const TABLE_MIN_SPAN = 4;

/** What decides how a panel may narrow: charts, tables, and the rest (notes, numbers). */
export type PanelShape = "time" | "table" | "text";
/**
 * The container widths of `main` the grid changes at: below `@3xl`, `@3xl` to `@5xl`, and
 * from `@5xl` (F4 T2.10b: screens lay out by the width they are given, not the window's).
 */
export type GridSize = "base" | "@3xl" | "@5xl";

export interface LayoutPanel {
  id: string;
  width: number;
  shape: PanelShape;
}

export interface LayoutCell {
  id: string;
  /** Columns of 12. */
  span: number;
}

function spanAt(panel: LayoutPanel, size: GridSize): number {
  if (size === "base") return COLUMNS;
  if (size === "@3xl" && panel.shape !== "text" && NARROW_WIDTHS.has(panel.width)) {
    return NARROW_SPAN;
  }
  if (size === "@5xl" && panel.shape === "table") return Math.max(panel.width, TABLE_MIN_SPAN);
  return Math.min(Math.max(panel.width, 1), COLUMNS);
}

export function layoutRows(panels: readonly LayoutPanel[], size: GridSize): LayoutCell[][] {
  const rows: LayoutCell[][] = [];
  let row: LayoutCell[] = [];
  let used = 0;
  for (const panel of panels) {
    const span = spanAt(panel, size);
    if (used + span > COLUMNS && row.length > 0) {
      rows.push(fill(row, used));
      row = [];
      used = 0;
    }
    row.push({ id: panel.id, span });
    used += span;
  }
  if (row.length > 0) rows.push(fill(row, used));
  return rows;
}

function fill(row: LayoutCell[], used: number): LayoutCell[] {
  const last = row[row.length - 1];
  return [...row.slice(0, -1), { ...last, span: last.span + COLUMNS - used }];
}
