import { describe, expect, it } from "vitest";

import { epochMillisToNanos, hrTimeToNanos } from "./time";

/**
 * The defect these guard: a nanosecond epoch is about 1.79e18, past JavaScript's
 * 2^53 safe integer. Built as a Number it is already rounded before
 * JSON.stringify runs, and the door accepts a timestamp that is quietly wrong.
 */

describe("epochMillisToNanos", () => {
  it("keeps every digit of a whole-millisecond epoch", () => {
    expect(epochMillisToNanos(1788626299167)).toBe("1788626299167000000");
  });

  it("keeps the sub-millisecond part a double can actually hold", () => {
    // A double at this magnitude resolves to about a quarter of a microsecond,
    // so nanosecond digits below that are not real in the input either.
    expect(epochMillisToNanos(1788626299167.25)).toBe("1788626299167250000");
  });

  it("carries a sub-millisecond remainder that rounds up into the next millisecond", () => {
    expect(epochMillisToNanos(1.9999996)).toBe("2000000");
  });
});

describe("hrTimeToNanos", () => {
  it("combines seconds and nanoseconds without leading zeros", () => {
    expect(hrTimeToNanos([1788626299, 167256064])).toBe("1788626299167256064");
  });

  it("pads a small nanosecond part into the right decimal place", () => {
    expect(hrTimeToNanos([1788626299, 7])).toBe("1788626299000000007");
  });

  it("carries digits a Number would have silently rounded away", () => {
    const nanos = hrTimeToNanos([1788626299, 167256064]);
    expect(nanos).toBe("1788626299167256064");
    // Round-tripping through Number is exactly the loss the string form avoids.
    expect(String(Number(nanos))).toBe("1788626299167256000");
  });
});
