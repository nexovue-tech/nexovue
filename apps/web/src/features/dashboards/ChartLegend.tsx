import { Link } from "@tanstack/react-router";

import { UNKNOWN } from "@/shared/format/number";
import { formatZonedTime } from "@/shared/format/zoned";
import { adoptScreenOverride } from "@/shared/selection";

import type { Marks, TimeChart } from "./time-lines";
import { marksAt } from "./time-lines";

/**
 * The value at the cursor, as text, under a chart (F20, FR32, FR35, T4.7).
 *
 * uPlot's own legend is off on dashboards: its labels hide or isolate a line on a click
 * and cannot be links. So the chart reports the cursor (`TrendChart`'s `readout`), and
 * this draws it. One line for a single line; for several, one row each, up to five, with
 * the height held so the panel does not move, each name a real link to its service, in
 * order of the value at the cursor. It also names what is at that moment: the finding,
 * the release (FR47, AC107). With no cursor, it reads the newest bucket, "so far" while
 * that bucket is still filling (FR66).
 */

/** Five rows at most: a top-N chart draws no more (FR6). */
const MOST_ROWS = 5;
const MS_PER_SECOND = 1000;

export interface ReadoutRow {
  name: string;
  service?: string;
  value: string;
  /** Its raw number, for ordering; null when this moment has none. */
  raw: number | null;
}

export interface Readout {
  time: string;
  soFar: boolean;
  rows: ReadoutRow[];
  marks: string[];
}

/** What the readout says at the cursor's bucket, or at the newest one without a cursor. */
export function readoutAt(
  chart: TimeChart,
  cursor: number | null,
  marks: Marks,
  zone: string,
): Readout | undefined {
  const atCursor = cursor !== null && cursor >= 0 && cursor < chart.x.length;
  const soFar = !atCursor && chart.soFar !== undefined;
  const index = atCursor ? cursor : chart.x.length - 1;
  const at = soFar ? chart.soFar?.at : chart.x[index];
  if (at === undefined) return undefined;
  const values = soFar
    ? (chart.soFar?.values ?? [])
    : chart.values.map((line) => line[index] ?? null);
  const rows = chart.lines.map((line, position) => {
    const raw = values[position] ?? null;
    return {
      name: line.name,
      ...(line.service === undefined ? {} : { service: line.service }),
      value: `${raw === null ? UNKNOWN : chart.format(raw)}${line.estimate ? " (est.)" : ""}`,
      raw,
    };
  });
  return {
    time: formatZonedTime(new Date(at * MS_PER_SECOND).toISOString(), { zone }),
    soFar,
    rows: chart.lines.length > 1 ? byValue(rows).slice(0, MOST_ROWS) : rows,
    marks: marksAt(marks, at, chart.bucketSeconds),
  };
}

/** Highest first; a moment with no reading goes last rather than reading as zero. */
function byValue(rows: ReadoutRow[]): ReadoutRow[] {
  return [...rows].sort((first, second) => (second.raw ?? -Infinity) - (first.raw ?? -Infinity));
}

export function ChartLegend({ readout, lines }: { readout: Readout | undefined; lines: number }) {
  const time = readout ? `${readout.time}${readout.soFar ? " so far" : ""}` : UNKNOWN;
  const marks = readout && readout.marks.length > 0 ? ` · ${readout.marks.join(" · ")}` : "";
  if (lines <= 1) {
    const [row] = readout?.rows ?? [];
    return (
      <p className="h-5 truncate font-mono text-2xs text-ink-dim">
        {time} · {row?.value ?? UNKNOWN}
        {marks}
      </p>
    );
  }
  const shown = Math.min(lines, MOST_ROWS);
  return (
    <div className="font-mono text-2xs text-ink-dim">
      <p className="h-5 truncate">
        {time}
        {marks}
      </p>
      {/* The rows' height is held, so the panel never moves as the cursor does. */}
      <ul style={{ minHeight: `${shown * 1.25}rem` }}>
        {(readout?.rows ?? []).map((row) => (
          <li key={row.service ?? row.name} className="flex h-5 items-baseline gap-2">
            {row.service === undefined ? (
              <span className="truncate">{row.name}</span>
            ) : (
              // A link, not a toggle: activating a name opens the service and hides no line (AC106).
              <Link
                to="/services/$name"
                params={{ name: row.service }}
                onClick={adoptScreenOverride}
                className="truncate text-ink underline hover:text-brand"
              >
                {row.name}
              </Link>
            )}
            <span className="ml-auto tabular-nums">{row.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
