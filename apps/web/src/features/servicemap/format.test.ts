import { describe, expect, it } from "vitest";

import { formatErrorRate, formatRate } from "./format";

describe("formatRate", () => {
  it("keeps a fraction visible below ten, where rounding would say zero", () => {
    expect(formatRate(0.4)).toBe("0.4");
    expect(formatRate(9.5)).toBe("9.5");
  });

  it("drops the point once the whole number carries the magnitude", () => {
    expect(formatRate(42.4)).toBe("42");
    expect(formatRate(999)).toBe("999");
  });

  it("abbreviates so a node holds a name and a number", () => {
    expect(formatRate(4798)).toBe("4.8k");
    expect(formatRate(2_000_000)).toBe("2M");
  });

  it("says zero rather than a unit for a window with no traffic", () => {
    expect(formatRate(0)).toBe("0");
    expect(formatRate(Number.NaN)).toBe("0");
  });
});

describe("formatErrorRate", () => {
  it("keeps the real demo failure rate legible instead of rounding it away", () => {
    expect(formatErrorRate(158 / 20_543)).toBe("0.77%");
  });

  it("says a rate is below the floor rather than calling it zero", () => {
    expect(formatErrorRate(0.0004)).toBe("<0.1%");
  });

  it("stops using decimals once the number is loud on its own", () => {
    expect(formatErrorRate(0.5)).toBe("50%");
  });

  it("reports no errors as no errors", () => {
    expect(formatErrorRate(0)).toBe("0%");
  });
});
