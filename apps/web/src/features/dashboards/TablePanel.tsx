import type { Column } from "@/shared/components/DataTable";
import { DataTable } from "@/shared/components/DataTable";
import { formatDuration } from "@/shared/format/duration";
import { formatCount, formatPercent, UNKNOWN } from "@/shared/format/number";
import { formatRatePerMinute } from "@/shared/format/rate";
import { visibleEscapes } from "@/shared/format/visible-escapes";
import { formatZonedTime } from "@/shared/format/zoned";

import type { TableRow } from "./api";

/**
 * A ranked table: its screen's own rows, in the store's order, cut to the panel's count
 * (F20, T5.2, T5.6a). Every telemetry value is text with hidden characters made visible
 * (FR61), and every latency is marked an estimate (AC113). A table's height follows its
 * rows, and it scrolls rather than squeezes (`DataTable`).
 */

const text = (value: unknown) => visibleEscapes(typeof value === "string" ? value : "");
const number = (value: unknown) => (typeof value === "number" ? value : null);

const rate = (key: string, header = "Requests/min"): Column<TableRow> => ({
  key,
  header,
  numeric: true,
  cell: (row) => formatRatePerMinute(number(row[key])),
});
const share: Column<TableRow> = {
  key: "error_rate",
  header: "Failure share",
  numeric: true,
  cell: (row) => formatPercent(number(row.error_rate)),
};
const p95: Column<TableRow> = {
  key: "p95_ms",
  header: "p95 (est.)",
  numeric: true,
  cell: (row) => formatDuration(number(row.p95_ms)),
};

export function tableColumns(kind: string, zone: string): readonly Column<TableRow>[] {
  switch (kind) {
    case "services_table":
      return [
        { key: "service", header: "Service", cell: (row) => text(row.service_name) },
        rate("requests_per_minute"),
        share,
        p95,
      ];
    case "endpoints_table":
      return [
        {
          key: "endpoint",
          header: "Endpoint",
          cell: (row) => `${text(row.service_name)} · ${text(row.endpoint)}`,
        },
        rate("requests_per_minute"),
        share,
        p95,
      ];
    case "dependencies_table":
      return [
        {
          key: "edge",
          header: "Dependency",
          cell: (row) => `${text(row.caller)} → ${text(row.callee)}`,
        },
        rate("calls_per_minute", "Calls/min"),
        share,
        p95,
      ];
    case "error_groups_table":
      return [
        {
          key: "error",
          header: "Error",
          cell: (row) => `${text(row.exception_type)}: ${text(row.message_template)}`,
        },
        {
          key: "occurrences",
          header: "Occurrences",
          numeric: true,
          cell: (row) => formatCount(number(row.occurrences)),
        },
        {
          key: "last_seen",
          header: "Last seen",
          cell: (row) =>
            typeof row.last_seen === "string" ? formatZonedTime(row.last_seen, { zone }) : UNKNOWN,
        },
      ];
    case "database_statements_table":
      return [
        { key: "hash", header: "Statement", cell: (row) => text(row.query_hash) },
        {
          key: "server",
          header: "Server",
          cell: (row) => {
            const server = row.server as { address?: unknown; port?: unknown } | undefined;
            return `${text(server?.address)}:${number(server?.port) ?? ""}`;
          },
        },
        {
          key: "worst",
          header: "Worst mean",
          numeric: true,
          cell: (row) => formatDuration(number(row.worst_mean_ms)),
        },
        {
          key: "latest",
          header: "Latest mean",
          numeric: true,
          cell: (row) => formatDuration(number(row.latest_mean_ms)),
        },
      ];
    case "web_vitals_table":
      return [
        { key: "vital", header: "Vital", cell: (row) => text(row.name) },
        { key: "p75", header: "p75 (est.)", numeric: true, cell: (row) => vitalValue(row) },
        {
          key: "readings",
          header: "Readings",
          numeric: true,
          cell: (row) => formatCount(number(row.readings)),
        },
      ];
    default:
      return [];
  }
}

/** CLS has no unit; every other vital is a time (the browser screen's own rule). */
function vitalValue(row: TableRow): string {
  const value = number(row.p75);
  if (value === null) return UNKNOWN;
  return row.name === "CLS" ? value.toFixed(2) : formatDuration(value);
}

function rowKey(row: TableRow, index: number): string {
  return `${index}-${JSON.stringify(row).slice(0, 64)}`;
}

export function TablePanel({
  kind,
  rows,
  caption,
  zone,
}: {
  kind: string;
  rows: readonly TableRow[];
  caption: string;
  zone: string;
}) {
  const keyed = rows.map((row, index) => ({ row, key: rowKey(row, index) }));
  const columns = tableColumns(kind, zone).map((column) => ({
    ...column,
    cell: (entry: { row: TableRow }) => column.cell(entry.row),
  }));
  return (
    <DataTable
      columns={columns}
      rows={keyed}
      rowKey={(entry) => entry.key}
      caption={caption}
      empty="Nothing in this window."
    />
  );
}
