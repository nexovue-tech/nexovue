import { describe, expect, it } from "vitest";

import { shortVersion, VERSION_SHOWN } from "./version";

describe("a version on one line", () => {
  it("leaves a short version as it is", () => {
    expect(shortVersion("v2.1.0")).toBe("v2.1.0");
  });

  it("keeps the start and the end of a long one, eliding the middle", () => {
    const long = `release-${"x".repeat(180)}-3f9c2a1`;
    const shown = shortVersion(long);
    expect(Array.from(shown)).toHaveLength(VERSION_SHOWN);
    expect(shown.startsWith("release-")).toBe(true);
    expect(shown.endsWith("3f9c2a1")).toBe(true);
    expect(shown).toContain("…");
  });

  it("counts characters, not code units, so a name outside ASCII is not cut in half", () => {
    const shown = shortVersion("版".repeat(40));
    expect(Array.from(shown)).toHaveLength(VERSION_SHOWN);
  });
});
