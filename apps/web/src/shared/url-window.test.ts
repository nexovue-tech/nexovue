import { describe, expect, it } from "vitest";

import {
  keepUrlWindow,
  parseUrlWindow,
  urlWindowApplies,
  urlWindowLine,
  urlWindowSearch,
  withoutUrlWindow,
} from "./url-window";

const FROM = "2026-11-09T13:47:00.000Z";
const TO = "2026-11-09T14:20:00.000Z";
const PROJECT = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0021";
const FINDING = "01a1c18a-3778-726a-ac51-855da271a38a";

describe("a window in the address (F3 FR16)", () => {
  it("reads from, to, project and finding, and writes them back as they were", () => {
    const window = parseUrlWindow({ from: FROM, to: TO, project: PROJECT, finding: FINDING });

    expect(window).toEqual({ from: FROM, to: TO, project: PROJECT, finding: FINDING });
    expect(urlWindowSearch(window)).toEqual({
      from: FROM,
      to: TO,
      project: PROJECT,
      finding: FINDING,
    });
  });

  it.each([
    ["no end", { from: FROM }],
    ["backwards", { from: TO, to: FROM }],
    ["no length", { from: FROM, to: FROM }],
    ["not a time", { from: "yesterday", to: TO }],
    ["longer than a month", { from: "2026-01-01T00:00:00Z", to: TO }],
    ["a number", { from: 1, to: 2 }],
  ])("is none when it cannot be read: %s", (_, search) => {
    expect(parseUrlWindow(search)).toBeNull();
  });

  it("keeps the minutes but drops a project or finding that is not an identifier", () => {
    const window = parseUrlWindow({ from: FROM, to: TO, project: "../admin", finding: "<b>" });

    expect(window).toEqual({ from: FROM, to: TO });
  });

  it("is what a screen keeps when it rewrites its filters, and nothing else", () => {
    const search = { from: FROM, to: TO, finding: FINDING, service: "checkout", junk: "x" };

    expect(keepUrlWindow(search)).toEqual({ from: FROM, to: TO, finding: FINDING });
    expect(keepUrlWindow({ service: "checkout" })).toEqual({});
  });

  it("is removed whole by 'Use my own window', leaving the filters", () => {
    expect(
      withoutUrlWindow({ from: FROM, to: TO, project: PROJECT, finding: FINDING, service: "x" }),
    ).toEqual({ service: "x" });
  });

  it.each([
    ["/traces", true],
    ["/logs", true],
    ["/errors", true],
    ["/infrastructure", true],
    ["/releases", true],
    ["/services/checkout", true],
    ["/services", false],
    ["/settings/channels", false],
    ["/", false],
  ])("applies on %s: %s", (path, applies) => {
    expect(urlWindowApplies(path)).toBe(applies);
  });

  it("says whose window it is, with one zone name, and wraps as words", () => {
    const now = Date.parse(TO);
    const finding = urlWindowLine(
      { from: FROM, to: TO, finding: FINDING },
      { zone: "Asia/Kolkata", now },
    );
    const plain = urlWindowLine({ from: FROM, to: TO }, { zone: "Asia/Kolkata", now });

    expect(finding).toBe("Showing this finding's window, 19:17 to 19:50 Kolkata time");
    expect(plain).toBe("Showing 19:17 to 19:50 Kolkata time");
  });
});
