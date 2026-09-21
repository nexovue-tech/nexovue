import { describe, expect, it } from "vitest";

import { formatDuration, formatDurationOn } from "./duration";
import { UNKNOWN } from "./number";

describe("formatDuration", () => {
  it("changes unit with the magnitude so the digit count stays scannable", () => {
    expect(formatDuration(0.4)).toBe("400µs");
    expect(formatDuration(12.34)).toBe("12.3ms");
    expect(formatDuration(1500)).toBe("1.5s");
    expect(formatDuration(90_000)).toBe("1m 30s");
  });

  it("shows a measured zero as zero", () => {
    expect(formatDuration(0)).toBe("0ms");
  });

  it("answers a duration we do not have with a rule: unmeasured is not instant", () => {
    expect(formatDuration(Number.NaN)).toBe(UNKNOWN);
    expect(formatDuration(-1)).toBe(UNKNOWN);
    expect(formatDuration(null)).toBe(UNKNOWN);
  });
});

describe("formatDurationOn", () => {
  it("writes zero in the unit the rest of the axis uses", () => {
    // The waterfall of a 492µs trace read "0ms · 123µs · 246µs · 369µs".
    expect(formatDurationOn(0, 0.492)).toBe("0µs");
    expect(formatDurationOn(0, 210)).toBe("0ms");
    expect(formatDurationOn(0, 4_200)).toBe("0s");
    expect(formatDurationOn(0, 90_000)).toBe("0s");
  });

  it("leaves every other tick to `formatDuration`", () => {
    expect(formatDurationOn(0.123, 0.492)).toBe("123µs");
    expect(formatDurationOn(210, 492)).toBe("210ms");
  });
});
