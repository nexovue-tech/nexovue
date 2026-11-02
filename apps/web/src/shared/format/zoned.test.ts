import { describe, expect, it } from "vitest";

import { UNKNOWN } from "./number";
import { browserTimeZone, formatCalendarDay, formatZonedTime, zoneLabel } from "./zoned";

const at = (iso: string, zone: string, now = iso) =>
  formatZonedTime(iso, { zone, now: Date.parse(now) });

describe("an instant on a named clock", () => {
  it("reads the same London wall clock on both sides of the clock change, named the same", () => {
    // British Summer Time ends at 01:00 UTC on 25 October 2026.
    expect(at("2026-10-24T13:02:00Z", "Europe/London")).toBe("14:02 London time");
    expect(at("2026-10-26T14:02:00Z", "Europe/London")).toBe("14:02 London time");
  });

  it("goes back an hour at the change itself, one real minute apart", () => {
    expect(at("2026-10-25T00:59:00Z", "Europe/London")).toBe("01:59 London time");
    expect(at("2026-10-25T01:00:00Z", "Europe/London")).toBe("01:00 London time");
  });

  it("names a plain abbreviation's own side of the change", () => {
    // US daylight time ends on 1 November 2026.
    expect(at("2026-10-31T18:02:00Z", "America/New_York")).toBe("14:02 EDT");
    expect(at("2026-11-02T19:02:00Z", "America/New_York")).toBe("14:02 EST");
  });

  it("names the city where the runtime offers only an offset", () => {
    // India's clock: "IST" is also Ireland's and Israel's, and the runtime says "GMT+5:30".
    expect(at("2026-10-31T08:32:00Z", "Asia/Kolkata")).toBe("14:02 Kolkata time");
  });

  it("names the city where the abbreviation is ambiguous", () => {
    // "CST" is Chicago, China and Cuba; "AST" is Halifax and Arabia.
    expect(at("2026-01-15T20:02:00Z", "America/Chicago")).toBe("14:02 Chicago time");
    expect(at("2026-07-15T19:02:00Z", "America/Chicago")).toBe("14:02 Chicago time");
    expect(zoneLabel("America/Halifax", new Date("2026-01-15T12:00:00Z"))).toBe("Halifax time");
  });

  it("writes a city the way it is said", () => {
    expect(zoneLabel("America/Argentina/Buenos_Aires", new Date("2026-10-31T12:00:00Z"))).toBe(
      "Buenos Aires time",
    );
  });

  it("names UTC and fixed offsets by themselves", () => {
    expect(at("2026-10-31T14:02:00Z", "UTC")).toBe("14:02 UTC");
    expect(at("2026-10-31T19:02:00Z", "Etc/GMT+5")).toBe("14:02 GMT-5");
  });

  it("falls back to a named UTC for a zone it does not know", () => {
    expect(at("2026-10-31T14:02:00Z", "Mars/Olympus_Mons")).toBe("14:02 UTC");
  });

  it("says the day when it is not today on that clock", () => {
    expect(at("2026-10-30T13:02:00Z", "Europe/London", "2026-10-31T09:00:00Z")).toBe(
      "30 Oct, 13:02 London time",
    );
    // 23:30 UTC is already tomorrow in Kolkata.
    expect(at("2026-10-31T23:30:00Z", "Asia/Kolkata", "2026-10-31T12:00:00Z")).toBe(
      "1 Nov, 05:00 Kolkata time",
    );
    expect(at("2025-12-31T14:02:00Z", "UTC", "2026-01-01T09:00:00Z")).toBe(
      "31 Dec 2025, 14:02 UTC",
    );
  });

  it("uses the browser's zone until a project names its own", () => {
    const now = Date.parse("2026-10-31T15:00:00Z");
    expect(formatZonedTime("2026-10-31T14:02:00Z", { now })).toBe(
      formatZonedTime("2026-10-31T14:02:00Z", { zone: browserTimeZone(), now }),
    );
  });

  it("shows an unreadable instant as absent", () => {
    expect(formatZonedTime("not a time", { zone: "UTC" })).toBe(UNKNOWN);
  });
});

describe("a day on the project's calendar", () => {
  it("reads as day and month", () => {
    expect(formatCalendarDay("2026-11-03")).toBe("3 Nov");
  });

  it("shows a day that does not exist as absent", () => {
    for (const day of ["2026-02-30", "2026-13-01", "3 Nov", ""]) {
      expect(formatCalendarDay(day)).toBe(UNKNOWN);
    }
  });
});
