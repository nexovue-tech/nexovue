import { describe, expect, it } from "vitest";

import { fromLocalInput, isUsableRange, toLocalInput } from "./local-instant";

describe("moving between an instant and a datetime-local input", () => {
  it("round-trips an instant through the input and back", () => {
    // The property that matters. Whatever zone the machine is in, what goes in
    // must be what comes out — a conversion that loses an hour is invisible on a
    // machine set to UTC, which is where most of us test.
    const instant = "2026-09-08T14:00:00.000Z";

    expect(fromLocalInput(toLocalInput(instant))).toBe(instant);
  });

  it("shows the reader their own wall clock, not UTC", () => {
    const shown = toLocalInput("2026-09-08T14:00:00.000Z");
    const expected = new Date("2026-09-08T14:00:00.000Z");

    expect(shown).toContain(String(expected.getFullYear()));
    expect(shown).toContain(`T${String(expected.getHours()).padStart(2, "0")}:`);
  });

  it("returns empty rather than a guess for an instant it cannot read", () => {
    expect(toLocalInput("not an instant")).toBe("");
  });

  it("returns null for a half-typed date instead of an instant in 1970", () => {
    expect(fromLocalInput("")).toBeNull();
    expect(fromLocalInput("2026-13-45T99:99")).toBeNull();
  });
});

describe("whether a pair can be used as a window", () => {
  const from = "2026-09-08T14:00:00.000Z";
  const to = "2026-09-08T15:00:00.000Z";

  it("accepts two instants in order", () => {
    expect(isUsableRange(from, to)).toBe(true);
  });

  it("refuses a pair that runs backwards", () => {
    expect(isUsableRange(to, from)).toBe(false);
  });

  it("refuses a window of no length", () => {
    // Literally an empty answer, but it reads on every screen as a mistake —
    // an empty list blaming the filters.
    expect(isUsableRange(from, from)).toBe(false);
  });

  it("refuses a half-filled pair", () => {
    expect(isUsableRange(from, null)).toBe(false);
    expect(isUsableRange(null, to)).toBe(false);
  });
});

describe("a dashboard's picker on the project's clock (F20, FR46, AC64)", () => {
  // 02:00 UTC is 07:30 in Kolkata, and a person reading 07:30 off a chart must get that hour.
  const KOLKATA = "Asia/Kolkata";

  it("shows an instant as the project's wall clock, whatever the browser's zone is", () => {
    expect(toLocalInput("2026-11-10T02:00:00.000Z", KOLKATA)).toBe("2026-11-10T07:30");
    expect(toLocalInput("2026-11-10T02:00:00.000Z", "America/New_York")).toBe("2026-11-09T21:00");
  });

  it("reads what a person types as that zone's clock", () => {
    expect(fromLocalInput("2026-11-10T07:30", KOLKATA)).toBe("2026-11-10T02:00:00.000Z");
    expect(fromLocalInput("2026-11-09T21:00", "America/New_York")).toBe("2026-11-10T02:00:00.000Z");
  });

  it("lands on the right side of a clock change", () => {
    // London leaves summer time at 02:00 on 25 Oct 2026: 00:30 that day is still BST (+1).
    expect(fromLocalInput("2026-10-25T00:30", "Europe/London")).toBe("2026-10-24T23:30:00.000Z");
    expect(fromLocalInput("2026-10-25T03:30", "Europe/London")).toBe("2026-10-25T03:30:00.000Z");
  });

  it("round-trips an instant through the picker", () => {
    const instant = "2026-03-29T10:15:00.000Z";
    expect(fromLocalInput(toLocalInput(instant, "Europe/London"), "Europe/London")).toBe(instant);
  });

  it("returns null for half-typed text in a zone too", () => {
    expect(fromLocalInput("2026-13-45T99:99", KOLKATA)).toBeNull();
    expect(fromLocalInput("", KOLKATA)).toBeNull();
  });
});
