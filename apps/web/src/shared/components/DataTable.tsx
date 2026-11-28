import type { ReactNode } from "react";

/**
 * History, as a table.
 *
 * **Charges, sessions and keys are records, and a record has columns.** They were
 * drawn as list rows with their fields run together into a sentence — "Chrome ·
 * Bengaluru · 2 hours ago" — which reads fine for one row and stops being
 * scannable at four, because nothing lines up between them.
 *
 * A real `<table>` rather than a grid of divs: the header cells are what a screen
 * reader announces for every value beneath them, and a list of divs announces
 * nothing. Scrolled rather than squeezed, because a timestamp column cannot be
 * narrowed into two words per line.
 */
export interface Column<Row> {
  key: string;
  header: string;
  /** Right-aligned for a figure, so the digits line up. Left for everything else. */
  numeric?: boolean;
  /** A header button that asks for this column's order. Needs the table's `sort`. */
  sortable?: boolean;
  /** Left out below `md`, for a row that says the rest in one of its other cells. */
  wideOnly?: boolean;
  cell: (row: Row) => ReactNode;
}

export type SortDirection = "ascending" | "descending";

/**
 * Which column the rows are in the order of, and what asking for another does. The table
 * orders nothing itself: the caller holds the rows, so it holds their order (F20, FR36).
 */
export interface TableSort {
  key: string;
  direction: SortDirection;
  onSort: (key: string) => void;
}

const SORT_MARK: Record<SortDirection, string> = { ascending: "\u25B2", descending: "\u25BC" };

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  caption,
  empty,
  sort,
}: {
  columns: ReadonlyArray<Column<Row>>;
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  /** What the table is, for anyone who cannot see where it sits on the page. */
  caption: string;
  /** Said instead of an empty table, so a blank area is not read as a failure. */
  empty: ReactNode;
  sort?: TableSort;
}) {
  if (rows.length === 0) {
    return <p className="py-3 text-ink-dim text-sm leading-relaxed">{empty}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th
                key={column.key}
                scope="col"
                aria-sort={column.sortable && sort ? ariaSort(sort, column.key) : undefined}
                className={`border-line border-b pt-0 pb-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps ${
                  column.numeric ? "pl-4 text-right" : "text-left"
                } ${gapAfter(index, columns.length)} ${column.wideOnly ? "hidden md:table-cell" : ""}`}
              >
                {column.sortable && sort ? (
                  <SortButton header={column.header} columnKey={column.key} sort={sort} />
                ) : (
                  column.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column, index) => (
                <td
                  key={column.key}
                  className={`border-line-subtle border-b py-2.5 align-top text-ink ${
                    column.numeric ? "pl-4 text-right tabular-nums" : ""
                  } ${gapAfter(index, columns.length)} ${column.wideOnly ? "hidden md:table-cell" : ""}`}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Every column but the last keeps a gap on its right, a right-aligned one included: its digits
 * end at the cell's edge, and without the gap the next column's words start against them
 * ("12" and "4 min. ago" read "124 min. ago").
 */
function gapAfter(index: number, count: number): string {
  return index < count - 1 ? "pr-4" : "";
}

/** Only the sorted header says a direction; the others say "none", so each carries the attribute. */
function ariaSort(sort: TableSort, key: string): "none" | SortDirection {
  return sort.key === key ? sort.direction : "none";
}

/** A real button, so the keyboard and a screen reader reach it as they reach any control. */
function SortButton({
  header,
  columnKey,
  sort,
}: {
  header: string;
  columnKey: string;
  sort: TableSort;
}) {
  const active = sort.key === columnKey;
  return (
    <button
      type="button"
      onClick={() => sort.onSort(columnKey)}
      className="inline-flex min-h-11 items-center gap-1 uppercase tracking-caps hover:text-ink focus-visible:outline-2 focus-visible:outline-brand md:min-h-0"
    >
      {header}
      <span aria-hidden="true" className={active ? "text-ink" : "opacity-0"}>
        {SORT_MARK[active ? sort.direction : "ascending"]}
      </span>
    </button>
  );
}
