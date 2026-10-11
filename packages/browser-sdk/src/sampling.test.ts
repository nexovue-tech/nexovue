import { describe, expect, it } from "vitest";

import { inSample } from "./sampling";

const ids = Array.from({ length: 10_000 }, (_, i) => `session-${i}-${i * 7919}`);

describe("session sampling (group 9, D4)", () => {
  it("keeps every session at the default rate of 1", () => {
    expect(ids.every((id) => inSample(id, 1))).toBe(true);
  });

  it("keeps none at 0", () => {
    expect(ids.some((id) => inSample(id, 0))).toBe(false);
  });

  it("keeps about the share asked for", () => {
    const kept = ids.filter((id) => inSample(id, 0.25)).length;
    expect(kept).toBeGreaterThan(2_300);
    expect(kept).toBeLessThan(2_700);
  });

  it("gives one session the same answer every time, so it is kept whole or not at all", () => {
    for (const id of ids.slice(0, 200)) {
      expect(inSample(id, 0.5)).toBe(inSample(id, 0.5));
    }
  });
});
