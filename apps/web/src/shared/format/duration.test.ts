import { describe, expect, it } from "vitest";

import { formatDuration } from "./duration";
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
