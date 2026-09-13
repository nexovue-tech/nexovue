// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import type { WindowChoice } from "./selection";
import {
  clearAbsoluteWindow,
  describeChoice,
  resolveChoice,
  SELECTION_STORAGE_KEY,
  setAbsoluteWindow,
  setSelectedRangeId,
  windowCacheKey,
} from "./selection";

const TUESDAY: WindowChoice = {
  kind: "absolute",
  from: "2026-09-08T14:00:00.000Z",
  to: "2026-09-08T15:00:00.000Z",
};

afterEach(() => {
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
