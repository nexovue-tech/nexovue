import { describe, expect, it } from "vitest";

import type { OkResult } from "./kept-readings";
import { keptLine, nextExpiry } from "./kept-readings";

/**
 * How long an earlier line stays after a failed read (F20, FR31, AC98, AC110): one refresh
 * interval, at most five minutes, on a live window; with no limit on a fixed one. Fixed
 * instants only.
 */

const reading = { state: "ok", kind: "service_trend" } as OkResult;
const FAILED_AT = 1_000_000;
const kept = { reading, failingSince: FAILED_AT };
const MINUTE = 60_000;

describe("a kept line", () => {
  it("stays one interval on a live window, and goes after it", () => {
    expect(keptLine(kept, MINUTE, FAILED_AT + MINUTE)).toBe(reading);
    expect(keptLine(kept, MINUTE, FAILED_AT + MINUTE + 1)).toBeUndefined();
  });

  it("stays at most five minutes whatever the interval", () => {
    expect(keptLine(kept, 15 * MINUTE, FAILED_AT + 5 * MINUTE + 1)).toBeUndefined();
  });

  it("stays with no time limit on a fixed window, which nothing about it made wrong (AC110)", () => {
    expect(keptLine(kept, "closed", FAILED_AT + 24 * 60 * MINUTE)).toBe(reading);
    expect(nextExpiry({ a: kept }, "closed", FAILED_AT)).toBeUndefined();
  });

  it("is not kept while the reading is current", () => {
    expect(keptLine({ reading, failingSince: null }, MINUTE, FAILED_AT)).toBeUndefined();
  });
});
