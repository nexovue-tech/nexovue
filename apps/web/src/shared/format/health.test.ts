import { describe, expect, it } from "vitest";

import { formatErrors } from "./health";
import { UNKNOWN } from "./number";

describe("formatErrors", () => {
  it("reads as a count and a share", () => {
    expect(formatErrors(158, 158 / 2867)).toBe("158 · 5.5%");
  });

  it("is a rule, not a zero: health is the absence of a number to read past", () => {
    expect(formatErrors(0, 0)).toBe(UNKNOWN);
  });

  it("is the same rule when the count never arrived, rather than half a cell", () => {
    expect(formatErrors(Number.NaN, 0.1)).toBe(UNKNOWN);
    expect(formatErrors(null, null)).toBe(UNKNOWN);
  });
});
