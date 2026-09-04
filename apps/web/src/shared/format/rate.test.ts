import { describe, expect, it } from "vitest";

import { UNKNOWN } from "./number";
import { formatCompactRate, formatRatePerMinute } from "./rate";

describe("formatRatePerMinute", () => {
  it("keeps a decimal while the rate is small enough for one to matter", () => {
    expect(formatRatePerMinute(2.25)).toBe("2.3");
    expect(formatRatePerMinute(9.94)).toBe("9.9");
  });

  it("rounds and groups once the rate is large", () => {
    expect(formatRatePerMinute(1234.6)).toBe("1,235");
  });

  it("marks a floor rather than rounding a live service down to nothing", () => {
    expect(formatRatePerMinute(0.02)).toBe("<0.1");
  });

  it("shows a real zero as zero", () => {
    expect(formatRatePerMinute(0)).toBe("0");
  });

  it("answers a rate that is not a number with a rule, not with silence", () => {
    expect(formatRatePerMinute(Number.NaN)).toBe(UNKNOWN);
    expect(formatRatePerMinute(null)).toBe(UNKNOWN);
  });
});

describe("formatCompactRate", () => {
  it("keeps a fraction visible below ten, where rounding would say zero", () => {
    expect(formatCompactRate(0.4)).toBe("0.4");
    expect(formatCompactRate(9.5)).toBe("9.5");
  });

  it("drops the point once the whole number carries the magnitude", () => {
    expect(formatCompactRate(42.4)).toBe("42");
    expect(formatCompactRate(999)).toBe("999");
  });

  it("abbreviates so a node holds a name and a number", () => {
    expect(formatCompactRate(4798)).toBe("4.8k");
    expect(formatCompactRate(2_000_000)).toBe("2M");
  });

  it("marks the same floor the table does, rather than drawing a live edge as idle", () => {
    expect(formatCompactRate(0.02)).toBe("<0.1");
  });

  it("says zero rather than a unit for a window with no traffic", () => {
    expect(formatCompactRate(0)).toBe("0");
  });

  it("answers a rate that is not a number with a rule, not with silence", () => {
    expect(formatCompactRate(Number.NaN)).toBe(UNKNOWN);
  });
});
