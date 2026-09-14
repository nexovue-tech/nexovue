import { describe, expect, it } from "vitest";

import type { LiveBuffer, LogLine } from "./log-feed";
import { acceptFrame, buildLogRows, describeLogs, EMPTY_LIVE } from "./log-feed";

function line(rowKey: string, body = `line ${rowKey}`): LogLine {
  return {
    row_key: rowKey,
    observed_at: "2026-08-30T08:02:01.690Z",
    service_name: "demo-payments",
    severity: "INFO",
    severity_number: 9,
    body,
    trace_id: "aa".repeat(16),
    span_id: "bb".repeat(8),
    attributes: {},
  };
}

function frame(rowKeys: string[], dropped = 0) {
  return { records: rowKeys.map((key) => line(key)), dropped };
}

function keysOf(buffer: LiveBuffer): string[] {
  return buffer.entries.map((entry) =>
    entry.kind === "line" ? entry.line.row_key : `gap(${entry.dropped})`,
  );
}

describe("acceptFrame", () => {
  it("puts the newest line at the top, whatever order the frame arrived in", () => {
    // The server sends a frame oldest-first; the screen reads newest-first.
    const buffer = acceptFrame(EMPTY_LIVE, frame(["1", "2", "3"]));
    expect(keysOf(buffer)).toEqual(["3", "2", "1"]);
  });

  it("prepends later frames above earlier ones", () => {
    const first = acceptFrame(EMPTY_LIVE, frame(["1", "2"]));
    const second = acceptFrame(first, frame(["3", "4"]));
    expect(keysOf(second)).toEqual(["4", "3", "2", "1"]);
  });

  it("marks a gap the server reported, between the frame and what came before", () => {
    const first = acceptFrame(EMPTY_LIVE, frame(["1", "2"]));
    const second = acceptFrame(first, frame(["8", "9"], 5));

    expect(keysOf(second)).toEqual(["9", "8", "gap(5)", "2", "1"]);
    expect(second.droppedTotal).toBe(5);
  });

  it("reports nothing when the server dropped nothing", () => {
    const buffer = acceptFrame(EMPTY_LIVE, frame(["1"]));
    expect(buffer.entries.every((entry) => entry.kind === "line")).toBe(true);
    expect(buffer.droppedTotal).toBe(0);
  });

  it("drops its own oldest lines past capacity and says how many", () => {
    const full = acceptFrame(EMPTY_LIVE, frame(["1", "2", "3", "4", "5"]), 3);

    expect(keysOf(full)).toEqual(["5", "4", "3", "gap(2)"]);
    expect(full.droppedTotal).toBe(2);
  });

  it("keeps dropping the oldest as later frames arrive", () => {
    let buffer = acceptFrame(EMPTY_LIVE, frame(["1", "2", "3"]), 3);
    buffer = acceptFrame(buffer, frame(["4", "5"]), 3);

    expect(keysOf(buffer)).toEqual(["5", "4", "3", "gap(2)"]);
    expect(buffer.droppedTotal).toBe(2);
  });

  it("folds a gap it discards into the one it leaves behind, losing no count", () => {
    let buffer = acceptFrame(EMPTY_LIVE, frame(["1", "2"]), 4);
    buffer = acceptFrame(buffer, frame(["3", "4"], 7), 4);
    // The frame's gap of 7 is now the oldest entry; adding two more lines pushes it out.
    buffer = acceptFrame(buffer, frame(["5", "6"]), 4);

    expect(keysOf(buffer)).toEqual(["6", "5", "4", "3", "gap(9)"]);
    expect(buffer.droppedTotal).toBe(9);
  });

  it("gives every gap its own key, so two gaps are two rows", () => {
    let buffer = acceptFrame(EMPTY_LIVE, frame(["1"], 2));
    buffer = acceptFrame(buffer, frame(["2"], 3));

    const gapKeys = buffer.entries.filter((entry) => entry.kind === "gap").map((gap) => gap.key);
    expect(gapKeys).toHaveLength(2);
    expect(new Set(gapKeys).size).toBe(2);
  });
});

describe("buildLogRows", () => {
  it("puts live lines above the searched page", () => {
    const live = acceptFrame(EMPTY_LIVE, frame(["9"]));
    const rows = buildLogRows(live, [line("2"), line("1")]);

    expect(rows.map((row) => row.key)).toEqual(["9", "2", "1"]);
  });

  it("shows a line once when the tail and the search both hold it", () => {
    // The tail starts a few seconds back, so an overlap is normal, not a bug.
    const live = acceptFrame(EMPTY_LIVE, frame(["2"]));
    const rows = buildLogRows(live, [line("2"), line("1")]);

    expect(rows.map((row) => row.key)).toEqual(["2", "1"]);
    expect(rows[0].kind === "line" && rows[0].live).toBe(true);
  });

  it("carries a gap through as a row of its own", () => {
    const live = acceptFrame(acceptFrame(EMPTY_LIVE, frame(["1"])), frame(["2"], 4));
    const rows = buildLogRows(live, []);

    expect(rows.map((row) => row.kind)).toEqual(["line", "gap", "line"]);
  });

  it("keeps both copies of a line the store really holds twice", () => {
    // `logs` has no primary key, so two byte-identical records share one row_key.
    // Collapsing them would delete a line that was genuinely written twice.
    const rows = buildLogRows(EMPTY_LIVE, [line("2"), line("2"), line("1")]);

    expect(rows).toHaveLength(3);
    expect(new Set(rows.map((row) => row.key)).size).toBe(3);
  });

  it("skips as many searched copies as the tail is holding, and no more", () => {
    const live = acceptFrame(EMPTY_LIVE, frame(["2"]));
    const rows = buildLogRows(live, [line("2"), line("2"), line("1")]);

    // One copy live, one copy stored, one older line — three rows, not two and not four.
    expect(rows.map((row) => row.kind === "line" && row.line.row_key)).toEqual(["2", "2", "1"]);
    expect(rows.map((row) => row.kind === "line" && row.live)).toEqual([true, false, false]);
  });

  it("never repeats a render key, whatever the store holds", () => {
    const live = acceptFrame(EMPTY_LIVE, frame(["7", "7"], 3));
    const rows = buildLogRows(live, [line("7"), line("7"), line("7")]);

    expect(new Set(rows.map((row) => row.key)).size).toBe(rows.length);
  });

  it("shows the searched page alone when the tail has sent nothing", () => {
    const rows = buildLogRows(EMPTY_LIVE, [line("1")]);
    expect(rows.map((row) => row.key)).toEqual(["1"]);
    expect(rows[0].kind === "line" && rows[0].live).toBe(false);
  });
});

describe("describeLogs", () => {
  it("says the tail is running", () => {
    expect(describeLogs(1240, "the last hour", "live", 0)).toBe(
      "1,240 lines from the last hour, newest first, updating as they arrive.",
    );
  });

  it("says the tail is paused", () => {
    expect(describeLogs(12, "the last hour", "paused", 0)).toContain("The tail is paused.");
  });

  it("states the loss once, in the sentence, as well as marking each gap", () => {
    expect(describeLogs(12, "the last hour", "live", 142)).toContain(
      "142 lines were dropped while the tail was behind",
    );
  });

  it("says nothing about a tail that is neither running nor paused", () => {
    const sentence = describeLogs(12, "the last hour", "off", 0);
    expect(sentence).toBe("12 lines from the last hour, newest first.");
    expect(sentence).not.toContain("paused");
  });

  it("counts one line as a line", () => {
    expect(describeLogs(1, "the last 15 minutes", "live", 1)).toContain(
      "1 line from the last 15 minutes",
    );
    expect(describeLogs(1, "the last 15 minutes", "live", 1)).toContain("1 line was dropped");
  });
});
