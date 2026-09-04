import { describe, expect, it } from "vitest";

import { formatBinaryBytes, formatDecimalBytes } from "./bytes";
import { UNKNOWN } from "./number";

describe("formatDecimalBytes", () => {
  it("steps up in decimal units, because that is what per-GB prices are quoted in", () => {
    expect(formatDecimalBytes(999)).toBe("999 B");
    expect(formatDecimalBytes(1_000)).toBe("1.0 kB");
    expect(formatDecimalBytes(42_100_000_000)).toBe("42.1 GB");
  });

  it("does not round bytes into a fraction of a byte", () => {
    expect(formatDecimalBytes(1)).toBe("1 B");
  });

  it("is a plain zero when nothing was ingested", () => {
    expect(formatDecimalBytes(0)).toBe("0 B");
  });
});

describe("formatBinaryBytes", () => {
  it("uses binary prefixes, the way every host tool quotes memory", () => {
    expect(formatBinaryBytes(8_600_219_648)).toBe("8.0 GiB");
    expect(formatBinaryBytes(5_943_672_832)).toBe("5.5 GiB");
  });

  it("leaves plain bytes unscaled", () => {
    expect(formatBinaryBytes(512)).toBe("512 B");
  });

  it("drops the decimal once three digits are already there", () => {
    expect(formatBinaryBytes(512 * 1024)).toBe("512 KiB");
  });
});

describe("the two unit systems are not interchangeable", () => {
  it("reads the same total differently, which is why the caller picks one", () => {
    expect(formatDecimalBytes(1_000)).toBe("1.0 kB");
    expect(formatBinaryBytes(1_000)).toBe("1000 B");
  });
});

describe("an absent byte total is never a zero", () => {
  it("answers null with a rule", () => {
    // "0 B" would report an unmonitored host as one using no memory.
    expect(formatBinaryBytes(null)).toBe(UNKNOWN);
    expect(formatDecimalBytes(null)).toBe(UNKNOWN);
  });

  it("answers a total that is not a number with a rule", () => {
    expect(formatDecimalBytes(Number.NaN)).toBe(UNKNOWN);
    expect(formatBinaryBytes(-1)).toBe(UNKNOWN);
  });
});
