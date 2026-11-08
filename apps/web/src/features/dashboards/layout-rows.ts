/**
 * Rows of a dashboard's grid, worked out in the client (F20, FR49, NFR6).
 *
 * A panel's `width` is its share of a 12-column row, and the last panel in each row grows
 * to fill it, so a saved layout never leaves empty columns on the right (the founder's
 * standing rule). Narrow charts and tables widen where the window is narrow: at 1024 px a
 * chart of width 3 would have about 114 px of plot, and a table scrolls rather than
 * squeezes (design, *Dashboard view*).
 */

export const COLUMNS = 12;
/** Between `lg` and `xl`, a chart or table of width 3 or 4 spans this many. */
const NARROW_SPAN = 6;
const NARROW_WIDTHS = new Set([3, 4]);
/** From `xl`, a table spans at least this many. */
const TABLE_MIN_SPAN = 4;

/** What decides how a panel may narrow: charts, tables, and the rest (notes, numbers). */
export type PanelShape = "time" | "table" | "text";
/** Tailwind's breakpoints the grid changes at: below `lg`, `lg` to `xl`, and from `xl`. */
export type GridSize = "base" | "lg" | "xl";

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
  if (size === "lg" && panel.shape !== "text" && NARROW_WIDTHS.has(panel.width)) {
    return NARROW_SPAN;
  }
  if (size === "xl" && panel.shape === "table") return Math.max(panel.width, TABLE_MIN_SPAN);
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
