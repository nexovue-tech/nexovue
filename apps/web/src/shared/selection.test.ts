// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import type { WindowChoice } from "./selection";
import {
  adoptScreenOverride,
  clearAbsoluteWindow,
  clearScreenOverride,
  DASHBOARD_RANGES,
  describeChoice,
  describeWindowChoice,
  readSelection,
  resolveChoice,
  SELECTION_STORAGE_KEY,
  setAbsoluteWindow,
  setScreenOverride,
  setSelectedProjectId,
  setSelectedRangeId,
  TIME_RANGES,
  windowCacheKey,
} from "./selection";

const TUESDAY: WindowChoice = {
  kind: "absolute",
  from: "2026-09-08T14:00:00.000Z",
  to: "2026-09-08T15:00:00.000Z",
};

afterEach(() => {
  clearScreenOverride();
  clearAbsoluteWindow();
  setSelectedRangeId("1h");
  localStorage.removeItem(SELECTION_STORAGE_KEY);
});

describe("what a query key carries for a window", () => {
  it("stays still while a relative window advances underneath it", () => {
    // The whole point. `to` is "now", so a key holding it changes every ten
    // seconds — and a changed key is a different query with no cached data,
    // which renders the skeleton. Every refresh was a cold load for that reason.
    const choice: WindowChoice = { kind: "relative", rangeId: "1h" };

    const early = windowCacheKey(choice);
    const late = windowCacheKey(choice);

    expect(early).toBe(late);
    expect(resolveChoice(choice, 1_000_000).to).not.toBe(resolveChoice(choice, 9_000_000).to);
  });

  it("tells two different ranges apart", () => {
    expect(windowCacheKey({ kind: "relative", rangeId: "1h" })).not.toBe(
      windowCacheKey({ kind: "relative", rangeId: "24h" }),
    );
  });

  it("tells an absolute window apart from a relative one", () => {
    expect(windowCacheKey(TUESDAY)).not.toBe(windowCacheKey({ kind: "relative", rangeId: "1h" }));
  });
});

describe("resolving a choice into instants", () => {
  it("hands an absolute window back untouched, whatever the clock says", () => {
    expect(resolveChoice(TUESDAY, Date.now())).toEqual({ from: TUESDAY.from, to: TUESDAY.to });
    expect(resolveChoice(TUESDAY, 0)).toEqual({ from: TUESDAY.from, to: TUESDAY.to });
  });

  it("moves a relative window with the clock", () => {
    const early = resolveChoice({ kind: "relative", rangeId: "1h" }, 1_000_000_000);
    const later = resolveChoice({ kind: "relative", rangeId: "1h" }, 1_000_600_000);

    expect(Date.parse(later.to)).toBeGreaterThan(Date.parse(early.to));
  });
});

describe("choosing a window", () => {
  it("refuses a pair that is not two instants in order", () => {
    // Written by anything with access to the key, or by a bad date field. Neither
    // should leave the console unable to read.
    setAbsoluteWindow("not a date", TUESDAY.to);
    expect(localStorage.getItem(SELECTION_STORAGE_KEY) ?? "").not.toContain("not a date");

    setAbsoluteWindow(TUESDAY.to, TUESDAY.from);
    expect(localStorage.getItem(SELECTION_STORAGE_KEY) ?? "").not.toContain('"absolute"');
  });

  it("remembers an absolute window across a reload", () => {
    setAbsoluteWindow(TUESDAY.from, TUESDAY.to);

    expect(localStorage.getItem(SELECTION_STORAGE_KEY)).toContain(TUESDAY.from);
  });

  it("returns to the range the reader was on rather than a default", () => {
    // Leaving an incident should put somebody back where they were, not at
    // "last hour" because that is what the code initialises to.
    setSelectedRangeId("24h");
    setAbsoluteWindow(TUESDAY.from, TUESDAY.to);

    clearAbsoluteWindow();

    expect(localStorage.getItem(SELECTION_STORAGE_KEY)).toContain("24h");
  });
});

describe("how a window reads", () => {
  it("says a relative window in the range picker's own words", () => {
    expect(describeChoice({ kind: "relative", rangeId: "24h" })).toBe("last 24 hours");
  });

  it("says an absolute window as two instants", () => {
    expect(describeChoice(TUESDAY)).toContain("to");
    expect(describeChoice(TUESDAY)).not.toContain("last");
  });
});

describe("the window a screen's sentences carry", () => {
  it("names the relative range the reader chose", () => {
    expect(describeWindowChoice({ kind: "relative", rangeId: "1h" })).toBe("the last hour");
  });

  it("names the pinned range instead of the range it replaced", () => {
    /* The regression. Twelve screens printed the *relative* label, so pinning a
       window left every subtitle in the console saying "the last 15 minutes"
       while the control above it said a range that ended hours ago. */
    const phrase = describeWindowChoice({
      kind: "absolute",
      from: "2026-09-14T12:00:00.000Z",
      to: "2026-09-14T13:00:00.000Z",
    });

    expect(phrase).not.toContain("the last");
    expect(phrase).toContain(" on ");
    // 24-hour, like every other clock in this console.
    expect(phrase).toMatch(/^\d\d:\d\d–\d\d:\d\d on /);
  });

  it("names both days when a pinned range crosses one", () => {
    /* "12:00–13:00 on 15 Sept" for a range that began the day before would lose
       the day it started, which is the only thing making it unambiguous. */
    // Twenty-six hours, so it crosses a day in the reader's zone whatever that is.
    const phrase = describeWindowChoice({
      kind: "absolute",
      from: "2026-09-14T00:00:00.000Z",
      to: "2026-09-15T02:00:00.000Z",
    });

    expect(phrase).toContain(" to ");
    expect(phrase).not.toContain(" on ");
  });

  it("says the range is pinned rather than printing Invalid Date", () => {
    expect(describeWindowChoice({ kind: "absolute", from: "nonsense", to: "also" })).toBe(
      "the pinned range",
    );
  });

  it("reads as a sentence in both cases, which is why it carries its own article", () => {
    /* Callers write "over {phrase}", never "over the last {phrase}" — the two
       cases do not fit one sentence otherwise. */
    for (const choice of [
      { kind: "relative", rangeId: "15m" } as const,
      {
        kind: "absolute",
        from: "2026-09-14T12:00:00.000Z",
        to: "2026-09-14T13:00:00.000Z",
      } as const,
    ]) {
      expect(`Every service you send traces from, over ${describeWindowChoice(choice)}.`).toMatch(
        /^Every service you send traces from, over \S.*\.$/,
      );
    }
  });
});

/**
 * A screen's override (F20, T4.5): what a dashboard opened from a link, or dragged to a
 * time, shows while it is mounted. The top bar reads the same value, so the header and
 * the data cannot disagree, and it is never remembered, so leaving the screen or
 * reloading elsewhere shows the reader's own choice (AC69's data half).
 */
describe("a screen's override", () => {
  const STAGING = "0190e3f4-0000-7000-8000-00000000000b";
  const PRODUCTION = "0190e3f4-0000-7000-8000-00000000000a";

  it("is what every reader sees while it holds, zone included", () => {
    setSelectedProjectId(PRODUCTION);
    setScreenOverride({ projectId: STAGING, window: TUESDAY, zone: "Asia/Kolkata" });

    expect(readSelection()).toMatchObject({
      projectId: STAGING,
      window: TUESDAY,
      zone: "Asia/Kolkata",
    });
  });

  it("is never remembered, even when the reader's own choice is", () => {
    setSelectedProjectId(PRODUCTION);
    setScreenOverride({ projectId: STAGING, window: TUESDAY });
    // A change of range on purpose is remembered, as on any screen; the override is not.
    setSelectedRangeId("24h");

    const stored = localStorage.getItem(SELECTION_STORAGE_KEY) ?? "";
    expect(stored).toContain(PRODUCTION);
    expect(stored).not.toContain(STAGING);
    expect(stored).not.toContain(TUESDAY.from);
  });

  it("gives way to the reader's own choice on the part they change", () => {
    setSelectedProjectId(PRODUCTION);
    setScreenOverride({ projectId: STAGING, window: TUESDAY });

    setSelectedRangeId("6h");

    expect(readSelection().window).toEqual({ kind: "relative", rangeId: "6h" });
    expect(readSelection().projectId).toBe(STAGING);
  });

  it("leaves the reader's own choice behind it when the screen goes", () => {
    setSelectedProjectId(PRODUCTION);
    setSelectedRangeId("24h");
    setScreenOverride({ projectId: STAGING, window: TUESDAY, zone: "Asia/Kolkata" });

    clearScreenOverride();

    expect(readSelection()).toMatchObject({
      projectId: PRODUCTION,
      window: { kind: "relative", rangeId: "24h" },
    });
    expect(readSelection().zone).toBeUndefined();
  });

  it("is ended by Back to live, even over a live window of the reader's own", () => {
    setScreenOverride({ window: TUESDAY });

    clearAbsoluteWindow();

    expect(readSelection().window.kind).toBe("relative");
  });

  it("becomes the reader's own choice when they follow a link out of the screen", () => {
    setSelectedProjectId(PRODUCTION);
    setScreenOverride({ projectId: STAGING, window: TUESDAY, zone: "Asia/Kolkata" });

    adoptScreenOverride();
    clearScreenOverride();

    expect(readSelection()).toMatchObject({ projectId: STAGING, window: TUESDAY });
    expect(readSelection().zone).toBeUndefined();
    expect(localStorage.getItem(SELECTION_STORAGE_KEY) ?? "").toContain(STAGING);
  });
});

describe("the window phrase on the project's clock (F20, FR46, AC64)", () => {
  const FROM = "2026-11-10T02:00:00.000Z";
  const TO = "2026-11-10T03:00:00.000Z";
  const window: WindowChoice = { kind: "absolute", from: FROM, to: TO };

  it("writes the times on the named zone, not the browser's", () => {
    expect(describeWindowChoice(window, "Asia/Kolkata")).toBe("07:30–08:30 on 10 Nov");
    expect(describeWindowChoice(window, "America/New_York")).toBe("21:00–22:00 on 9 Nov");
  });

  it("names the day at each end when the zone's day changes across the range", () => {
    const across: WindowChoice = {
      kind: "absolute",
      from: "2026-11-10T18:00:00.000Z",
      to: "2026-11-10T19:00:00.000Z",
    };
    // 23:30 to 00:30 in Kolkata: two days, so each end says its own.
    expect(describeWindowChoice(across, "Asia/Kolkata")).toBe("10 Nov, 23:30 to 11 Nov, 00:30");
  });

  it("offers 30 days to a dashboard and never reads one back from storage (FR52)", () => {
    expect(DASHBOARD_RANGES.map((range) => range.id)).toEqual([
      ...TIME_RANGES.map((r) => r.id),
      "30d",
    ]);
    expect(TIME_RANGES.some((range) => range.id === "30d")).toBe(false);
    expect(describeWindowChoice({ kind: "relative", rangeId: "30d" })).toBe("the last 30 days");
    expect(resolveChoice({ kind: "relative", rangeId: "30d" }, Date.parse(TO)).from).toBe(
      new Date(Math.ceil(Date.parse(TO) / 10_000) * 10_000 - 30 * 24 * 60 * 60_000).toISOString(),
    );
  });

  it("holds the screen's clock while it overrides, and drops it on leaving", () => {
    setScreenOverride({ zone: "Asia/Kolkata" });
    expect(readSelection().zone).toBe("Asia/Kolkata");
    clearScreenOverride();
    expect(readSelection().zone).toBeUndefined();
  });
});
