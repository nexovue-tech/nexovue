import { describe, expect, it } from "vitest";

import { previousWindow, resolveWindow, timeRangeById } from "./selection";

const BUCKET_MS = 10_000;

describe("resolveWindow", () => {
  /**
   * The regression: `to` was floored to the bucket below `now`, so the list silently
   * excluded everything from the last 0–10 seconds — the newest traces, which is the
   * only reason anyone opens the screen.
   */
  it("never ends before now, even mid-bucket", () => {
    const now = Date.parse("2026-08-27T08:00:32.276Z");
    expect(Date.parse(resolveWindow("1h", now).to)).toBeGreaterThanOrEqual(now);
  });

  it("includes a trace that arrived a moment ago", () => {
    const now = Date.parse("2026-08-27T08:00:32.276Z");
    const justNow = now - 500;
    const { from, to } = resolveWindow("1h", now);
    expect(Date.parse(from)).toBeLessThanOrEqual(justNow);
    expect(Date.parse(to)).toBeGreaterThanOrEqual(justNow);
  });

  it("stays on a bucket boundary, so the query key holds still between ticks", () => {
    const base = Date.parse("2026-08-27T08:00:30.000Z");
    const first = resolveWindow("1h", base + 1);
    const later = resolveWindow("1h", base + BUCKET_MS - 1);
    expect(later).toEqual(first);
  });

  it("advances once the bucket turns over", () => {
    const base = Date.parse("2026-08-27T08:00:30.000Z");
    expect(resolveWindow("1h", base + BUCKET_MS + 1)).not.toEqual(resolveWindow("1h", base + 1));
  });

  it("spans exactly the selected range", () => {
    const { from, to } = resolveWindow("15m", Date.parse("2026-08-27T08:00:00.000Z"));
    expect(Date.parse(to) - Date.parse(from)).toBe(timeRangeById("15m").minutes * 60_000);
  });

  it("falls back to a known range rather than producing a NaN window", () => {
    const { from, to } = resolveWindow("not-a-range", Date.parse("2026-08-27T08:00:00.000Z"));
    expect(Number.isNaN(Date.parse(from))).toBe(false);
    expect(Number.isNaN(Date.parse(to))).toBe(false);
  });
});

describe("previousWindow", () => {
  it("is the same span immediately before this one", () => {
    // An hour is compared with the hour before it. A fixed offset would compare
    // a Monday morning with a Sunday night and call the difference a change.
    expect(
      previousWindow({ from: "2026-09-13T10:00:00.000Z", to: "2026-09-13T11:00:00.000Z" }),
    ).toEqual({ from: "2026-09-13T09:00:00.000Z", to: "2026-09-13T10:00:00.000Z" });
  });

  it("ends exactly where this window begins, leaving no gap and no overlap", () => {
    const window = { from: "2026-09-13T10:17:30.000Z", to: "2026-09-13T10:32:30.000Z" };

    expect(previousWindow(window).to).toBe(window.from);
  });

  it("follows a window someone narrowed by hand rather than a named range", () => {
    const sevenMinutes = { from: "2026-09-13T10:00:00.000Z", to: "2026-09-13T10:07:00.000Z" };

    expect(previousWindow(sevenMinutes).from).toBe("2026-09-13T09:53:00.000Z");
  });

  it("returns the window unchanged rather than an invalid one it cannot compute", () => {
    const unparseable = { from: "not a time", to: "2026-09-13T11:00:00.000Z" };

    expect(previousWindow(unparseable)).toEqual(unparseable);
  });
});
