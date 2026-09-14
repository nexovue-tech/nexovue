/**
 * The lines on screen, as a file the browser saves without asking the server.
 *
 * **This is not the same answer as `logs/export.csv`, and both exist on purpose.**
 * Records reach ClickHouse through the pipeline, so the stored search runs behind
 * the tail: an export that reads only storage silently drops the newest lines, on
 * the one screen where the newest lines are the point. This one is exactly what
 * the reader is looking at — capped at `LIVE_CAPACITY`, and no further back.
 *
 * A blob rather than a link, which is the opposite of the choice `traces/api.ts`
 * records, and for the same reason read the other way: there is no server answer
 * to link to, because the rows never left this tab.
 */

import type { LiveBuffer, LogLine } from "./log-feed";

/** The API's own column names, so the two files open the same way. */
export const BUFFER_COLUMNS = [
  "observed_at",
  "severity",
  "severity_number",
  "service_name",
  "body",
  "trace_id",
  "span_id",
  "attributes",
] as const;

/** A field that could be read as anything but text, quoted the way CSV requires. */
function cell(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function row(line: LogLine): string {
  return [
    line.observed_at,
    line.severity,
    line.severity_number,
    line.service_name,
    line.body,
    line.trace_id,
    line.span_id,
    // One column rather than a column per key, matching the server export: log
    // attributes are per-record, so a spread would be as wide as the union of
    // every record in the file and empty almost everywhere.
    Object.keys(line.attributes).length > 0 ? JSON.stringify(line.attributes) : "",
  ]
    .map(cell)
    .join(",");
}

/**
 * The buffer as CSV, newest first — the order the screen renders.
 *
 * **Gap markers are not rows.** A gap is the screen saying it knows lines are
 * missing; writing it into a spreadsheet would turn "we lost some" into a record
 * that looks like a log line. The count is reported beside the control instead.
 */
export function bufferAsCsv(buffer: LiveBuffer): string {
  const lines = buffer.entries.filter((entry) => entry.kind === "line");
  return [BUFFER_COLUMNS.join(","), ...lines.map((entry) => row(entry.line))].join("\r\n");
}

/** How many log lines the file would hold, which the control has to say first. */
export function bufferLineCount(buffer: LiveBuffer): number {
  return buffer.entries.reduce((total, entry) => total + (entry.kind === "line" ? 1 : 0), 0);
}

/**
 * Hand the file to the browser.
 *
 * Separated from the conversion above so the conversion can be tested without a
 * DOM, and so this stays the only part that touches one.
 */
export function downloadBuffer(buffer: LiveBuffer, filename: string): void {
  const blob = new Blob([bufferAsCsv(buffer)], { type: "text/csv;charset=utf-8" });
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(href);
}
