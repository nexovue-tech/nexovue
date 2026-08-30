import { describe, expect, it } from "vitest";

import {
  describeVolume,
  formatCount,
  formatErrorRate,
  formatErrors,
  formatRatePerMinute,
} from "./health-format";

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

  it("survives a non-finite rate rather than printing NaN", () => {
    expect(formatRatePerMinute(Number.NaN)).toBe("0");
  });
});

describe("formatErrorRate", () => {
  it("turns the wire's fraction into a percentage", () => {
    expect(formatErrorRate(0.0551)).toBe("5.5%");
    expect(formatErrorRate(1)).toBe("100.0%");
  });

  it("marks a floor rather than reporting a failing service as 0.0%", () => {
    expect(formatErrorRate(0.0002)).toBe("<0.1%");
  });

  it("is a plain zero when nothing failed", () => {
    expect(formatErrorRate(0)).toBe("0%");
  });
});

describe("formatErrors", () => {
  it("reads as a count and a share", () => {
    expect(formatErrors(158, 158 / 2867)).toBe("158 · 5.5%");
  });

  it("is a rule, not a zero: health is the absence of a number to read past", () => {
    expect(formatErrors(0, 0)).toBe("—");
  });
});

describe("formatCount", () => {
  it("groups thousands in a fixed locale", () => {
    expect(formatCount(2867)).toBe("2,867");
  });
});

describe("describeVolume", () => {
  it("names the volume the rate came from", () => {
    expect(describeVolume(2867)).toBe("2,867 requests in this window");
  });

  it("agrees with itself in the singular", () => {
    expect(describeVolume(1)).toBe("1 request in this window");
  });
});
