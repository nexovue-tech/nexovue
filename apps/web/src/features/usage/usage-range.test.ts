import { describe, expect, it } from "vitest";

import { DEFAULT_RANGE_DAYS, labelForDays, RANGE_OPTIONS, usageRange, utcDay } from "./usage-range";

/** Late evening in UTC, which is the next day in half the world. */
const AUGUST_30 = Date.parse("2026-08-30T23:30:00Z");

describe("usageRange", () => {
  it("includes today at both ends, so seven days is today and the six before it", () => {
    const range = usageRange(7, AUGUST_30);

    expect(range.to).toBe("2026-08-30");
    expect(range.from).toBe("2026-08-24");
  });

  it("crosses a month boundary by day arithmetic, not by subtracting a month", () => {
    expect(usageRange(30, AUGUST_30).from).toBe("2026-08-01");
    expect(usageRange(90, AUGUST_30).from).toBe("2026-06-02");
  });

  it("carries the label the button shows, so the sentence and the control agree", () => {
    expect(usageRange(30, AUGUST_30).label).toBe("30 days");
  });

  it("offers a default that is one of the options", () => {
    expect(RANGE_OPTIONS.map((option) => option.days)).toContain(DEFAULT_RANGE_DAYS);
  });

  it("names a range nobody offered rather than mislabelling it", () => {
    expect(labelForDays(14)).toBe("14 days");
  });
});

describe("utcDay", () => {
  it("is the meter's day, not the reader's", () => {
    // 23:30 UTC is already the 31st in Sydney. Asking in the reader's zone would
    // ask the API for a day it has not counted, and read the answer as a quiet one.
    expect(utcDay(AUGUST_30)).toBe("2026-08-30");
  });
});
