import { useState } from "react";

import type { FrameIdentity } from "@/shared/components/FlameGraph";
import type { FunctionShare } from "./api";
import { formatShare } from "./profile-words";

type SortKey = "self" | "total" | "name";

/** How many rows are drawn before the reader asks for the rest. */
const FIRST_ROWS = 50;

/**
 * The functions that spent the most, as a table: self is time in the function's own
 * code, total includes what it called. Picking a row highlights that function
 * wherever it appears in the graph above (FR8).
 */
export function FunctionsTable({
  functions,
  total,
  format,
  search,
  selected,
  onSelect,
}: {
  functions: readonly FunctionShare[];
  total: number;
  format: (value: number) => string;
  search: string;
  selected: FrameIdentity | null;
  onSelect: (frame: FrameIdentity | null) => void;
}) {
  const [sort, setSort] = useState<SortKey>("self");
  const [showAll, setShowAll] = useState(false);
  const term = search.trim().toLowerCase();

  const sorted = [...functions].sort(ORDER[sort]);
  const rows = showAll ? sorted : sorted.slice(0, FIRST_ROWS);

  return (
    <section aria-labelledby="profile-functions" className="flex flex-col gap-2">
      <h2 id="profile-functions" className="font-medium font-serif text-base text-ink">
        Functions
      </h2>
      <p className="text-ink-dim text-xs">
        Self is what the function spent in its own code; total includes what it called. Pick a row
        to find it in the graph.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">The functions that spent the most</caption>
          <thead>
            <tr>
              <SortHeader label="Function" own="name" sort={sort} onSort={setSort} />
              <SortHeader label="Self" own="self" sort={sort} onSort={setSort} numeric />
              <SortHeader label="Total" own="total" sort={sort} onSort={setSort} numeric />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const picked =
                selected !== null && selected.name === row.name && selected.file === row.file;
              const matches = term !== "" && row.name.toLowerCase().includes(term);
              return (
                <tr
                  key={`${row.name}|${row.file}|${row.unsymbolized}`}
                  className={`border-line-subtle border-b ${picked || matches ? "bg-hover" : ""}`}
                >
                  <td className="max-w-0 truncate py-1.5 pr-4">
                    <button
                      type="button"
                      aria-pressed={picked}
                      // A long name is cut to the column; the whole of it is one hover away.
                      title={row.file ? `${row.name} — ${row.file}` : row.name}
                      onClick={() => onSelect(picked ? null : { name: row.name, file: row.file })}
                      className={`truncate text-left hover:underline focus-visible:outline-2 focus-visible:outline-brand ${
                        row.unsymbolized ? "text-ink-dim italic" : "text-ink"
                      }`}
                    >
                      {row.unsymbolized ? `${row.name} (unsymbolized)` : row.name}
                    </button>
                    {row.file ? (
                      <span className="ml-2 text-ink-dim text-xs">{row.file}</span>
                    ) : null}
                  </td>
                  <td className="whitespace-nowrap py-1.5 pl-4 text-right text-ink tabular-nums">
                    {format(row.self_value)} · {formatShare(row.self_value, total)}
                  </td>
                  <td className="whitespace-nowrap py-1.5 pl-4 text-right text-ink-dim tabular-nums">
                    {format(row.total)} · {formatShare(row.total, total)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!showAll && sorted.length > FIRST_ROWS ? (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="self-start text-brand-text text-sm hover:underline"
        >
          Show all {sorted.length.toLocaleString("en")} functions
        </button>
      ) : null}
    </section>
  );
}

const ORDER: Record<SortKey, (a: FunctionShare, b: FunctionShare) => number> = {
  self: (a, b) => b.self_value - a.self_value || b.total - a.total,
  total: (a, b) => b.total - a.total || b.self_value - a.self_value,
  name: (a, b) => a.name.localeCompare(b.name),
};

function SortHeader({
  label,
  own,
  sort,
  onSort,
  numeric = false,
}: {
  label: string;
  own: SortKey;
  sort: SortKey;
  onSort: (key: SortKey) => void;
  numeric?: boolean;
}) {
  const active = sort === own;
  return (
    <th
      scope="col"
      aria-sort={active ? (own === "name" ? "ascending" : "descending") : "none"}
      className={`border-line border-b pb-1.5 font-mono text-2xs text-ink-dim uppercase tracking-caps ${
        numeric ? "w-40 whitespace-nowrap pl-4 text-right" : "pr-4 text-left"
      }`}
    >
      <button
        type="button"
        onClick={() => onSort(own)}
        className={`uppercase hover:text-ink ${active ? "text-ink" : ""}`}
      >
        {label}
      </button>
    </th>
  );
}
