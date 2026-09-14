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
  cell: (row: Row) => ReactNode;
}

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  caption,
  empty,
}: {
  columns: ReadonlyArray<Column<Row>>;
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  /** What the table is, for anyone who cannot see where it sits on the page. */
  caption: string;
  /** Said instead of an empty table, so a blank area is not read as a failure. */
  empty: ReactNode;
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
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`border-line border-b pt-0 pb-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps ${
                  column.numeric ? "pl-4 text-right" : "pr-4 text-left"
                }`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`border-line-subtle border-b py-2.5 align-top text-ink ${
                    column.numeric ? "pl-4 text-right tabular-nums" : "pr-4"
                  }`}
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
