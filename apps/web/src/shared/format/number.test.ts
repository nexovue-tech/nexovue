import { describe, expect, it } from "vitest";

import { formatCount, formatPercent, trimTrailingZeros, UNKNOWN } from "./number";

describe("a figure we do not have is a rule, never a zero", () => {
  it("answers a count that is not a number with a rule", () => {
    // "0" here reads as "nothing happened", which is the opposite of "we cannot see".
    expect(formatCount(Number.NaN)).toBe(UNKNOWN);
    expect(formatCount(Number.POSITIVE_INFINITY)).toBe(UNKNOWN);
    expect(formatCount(null)).toBe(UNKNOWN);
    expect(formatCount(undefined)).toBe(UNKNOWN);
  });

  it("answers a negative count with a rule, because arithmetic broke rather than fell", () => {
    expect(formatCount(-4)).toBe(UNKNOWN);
  });

  it("answers a share that is not a number with a rule", () => {
    expect(formatPercent(Number.NaN)).toBe(UNKNOWN);
    expect(formatPercent(null)).toBe(UNKNOWN);
    expect(formatPercent(-0.5)).toBe(UNKNOWN);
  });

  it("still shows a genuine zero as zero", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatPercent(0)).toBe("0.0%");
  });
});

describe("formatCount", () => {
  it("groups thousands in a fixed locale", () => {
    expect(formatCount(158)).toBe("158");
    expect(formatCount(2867)).toBe("2,867");
    expect(formatCount(12_483)).toBe("12,483");
    expect(formatCount(90_000)).toBe("90,000");
    expect(formatCount(1_234_567)).toBe("1,234,567");
  });

  it("rounds to a whole, because a count is not a fraction", () => {
    expect(formatCount(1234.6)).toBe("1,235");
  });
});

describe("formatPercent", () => {
  it("turns the wire's fraction into a percentage", () => {
    expect(formatPercent(0.23575)).toBe("23.6%");
    expect(formatPercent(0.0551)).toBe("5.5%");
    expect(formatPercent(0.5)).toBe("50.0%");
    expect(formatPercent(1)).toBe("100.0%");
  });

  it("marks a floor rather than reporting a failing service as 0.0%", () => {
    expect(formatPercent(0.0002)).toBe("<0.1%");
    expect(formatPercent(0.0004)).toBe("<0.1%");
  });

  it("keeps a real low failure rate legible instead of rounding it away", () => {
    // 158 calls in 20,543 is the demo's own dependency failure rate.
    expect(formatPercent(158 / 20_543)).toBe("0.8%");
  });

  it("does not clamp an impossible figure — hiding it is how a wrong number survives", () => {
    expect(formatPercent(11.247)).toBe("1124.7%");
  });
});

describe("trimTrailingZeros", () => {
  it("drops noise after the point and keeps every digit before it", () => {
    expect(trimTrailingZeros("2.0")).toBe("2");
    expect(trimTrailingZeros("4.80")).toBe("4.8");
    expect(trimTrailingZeros("1200")).toBe("1200");
  });
});
