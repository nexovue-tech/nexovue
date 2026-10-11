import { describe, expect, it } from "vitest";

import { BUFFER_COLUMNS, bufferAsCsv, bufferLineCount } from "./log-export";
import type { LiveBuffer, LogLine } from "./log-feed";

function line(overrides: Partial<LogLine> = {}): LogLine {
  return {
    row_key: "1",
    observed_at: "2026-09-14T13:22:54.537Z",
    service_name: "demo-payments",
    severity: "ERROR",
    severity_number: 17,
    body: "payment authorization failed",
    trace_id: "aa".repeat(16),
    span_id: "bb".repeat(8),
    attributes: {},
    ...overrides,
  };
}

function buffer(entries: LiveBuffer["entries"]): LiveBuffer {
  return { entries, droppedTotal: 0 };
}

describe("the buffer as a file", () => {
  it("names the same columns the server export does", () => {
    const sheet = bufferAsCsv(buffer([{ kind: "line", key: "a", line: line() }]));

    expect(sheet.split("\r\n")[0]).toBe(BUFFER_COLUMNS.join(","));
  });

  it("keeps the newest line, which is the one storage does not have yet", () => {
    /* The whole reason this exists beside `logs/export.csv`: records reach
       ClickHouse through the pipeline, so the stored search runs behind the tail. */
    const newest = line({ row_key: "2", body: "arrived a second ago" });
    const sheet = bufferAsCsv(
      buffer([
        { kind: "line", key: "b", line: newest },
        { kind: "line", key: "a", line: line() },
      ]),
    );

    expect(sheet.split("\r\n")[1]).toContain("arrived a second ago");
  });

  it("quotes a body carrying a comma rather than splitting the row", () => {
    const sheet = bufferAsCsv(
      buffer([{ kind: "line", key: "a", line: line({ body: 'GET /a, "b"' }) }]),
    );

    expect(sheet.split("\r\n")[1]).toContain('"GET /a, ""b"""');
  });

  it("writes a body a spreadsheet would run as text, as the server export does", () => {
    const sheet = bufferAsCsv(buffer([{ kind: "line", key: "a", line: line({ body: "=1+2" }) }]));

    expect(sheet.split("\r\n")[1]).toContain(",'=1+2,");
  });

  it("writes attributes as one column, matching the server export", () => {
    const sheet = bufferAsCsv(
      buffer([{ kind: "line", key: "a", line: line({ attributes: { provider: "stripe" } }) }]),
    );

    expect(sheet).toContain('"{""provider"":""stripe""}"');
  });

  it("does not write a gap marker as if it were a log line", () => {
    /* A gap is the screen saying it knows lines are missing. In a spreadsheet
       that would read as a record of something that happened. */
    const sheet = bufferAsCsv(
      buffer([
        { kind: "line", key: "a", line: line() },
        { kind: "gap", key: "g", dropped: 40 },
      ]),
    );

    expect(sheet.split("\r\n")).toHaveLength(2);
    expect(sheet).not.toContain("40");
  });

  it("counts only lines, so the control does not promise rows it will not write", () => {
    const held = buffer([
      { kind: "line", key: "a", line: line() },
      { kind: "gap", key: "g", dropped: 40 },
      { kind: "line", key: "b", line: line({ row_key: "2" }) },
    ]);

    expect(bufferLineCount(held)).toBe(2);
  });
});
