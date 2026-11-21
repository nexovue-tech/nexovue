import type { Column } from "@/shared/components/DataTable";
import { DataTable } from "@/shared/components/DataTable";
import { UNKNOWN } from "@/shared/format/number";
import { formatZonedTime } from "@/shared/format/zoned";

import type { Marks, TimeChart } from "./time-lines";
import { marksAt } from "./time-lines";

/**
 * A chart's numbers as a table (F20, FR34, T4.8): the same readings, one row a bucket,
 * newest first, for anyone who cannot use a canvas and for reading exact values (AC34).
 * Latency is marked as an estimate in its header (AC113); a column names the finding or
 * release at each time, since shading cannot take focus (FR47, AC107); the bucket still
 * filling is its own row, "so far" (FR66).
 */

const MS_PER_SECOND = 1000;

export interface TableRow {
  key: string;
  time: string;
  values: string[];
  marks: string;
}

/** The table's rows, newest first, as plain data. */
export function tableRows(chart: TimeChart, marks: Marks, zone: string): TableRow[] {
  const row = (at: number, values: (number | null)[], soFar: boolean): TableRow => ({
    key: String(at),
    time: `${formatZonedTime(new Date(at * MS_PER_SECOND).toISOString(), { zone })}${soFar ? " so far" : ""}`,
    values: values.map((value) => (value === null ? UNKNOWN : chart.format(value))),
    marks: marksAt(marks, at, chart.bucketSeconds).join(" · "),
  });
  const drawn = chart.x.map((at, index) =>
    row(
      at,
      chart.values.map((line) => line[index] ?? null),
      false,
    ),
  );
  const filling = chart.soFar ? [row(chart.soFar.at, chart.soFar.values, true)] : [];
  return [...drawn, ...filling].reverse();
}

/** The headers, latency marked as an estimate wherever it is written (AC113). */
export function tableHeaders(chart: TimeChart): string[] {
  return chart.lines.map((line) => `${line.name}${line.estimate ? " (est.)" : ""}`);
}

export function ChartTable({
  chart,
  marks,
  zone,
  caption,
}: {
  chart: TimeChart;
  marks: Marks;
  zone: string;
  caption: string;
}) {
  const headers = tableHeaders(chart);
  const columns: Column<TableRow>[] = [
    { key: "time", header: "Time", cell: (row) => row.time },
    ...headers.map((header, index) => ({
      key: `line-${index}`,
      header,
      numeric: true,
      cell: (row: TableRow) => row.values[index] ?? UNKNOWN,
    })),
    { key: "marks", header: "Finding or release", cell: (row) => row.marks },
  ];
  return (
    <DataTable
      columns={columns}
      rows={tableRows(chart, marks, zone)}
      rowKey={(row) => row.key}
      caption={caption}
      empty="No readings in this window."
    />
  );
}
