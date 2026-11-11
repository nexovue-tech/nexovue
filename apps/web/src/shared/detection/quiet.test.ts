import { describe, expect, it } from "vitest";
import { quietEffect } from "./quiet";

describe("what a snooze and a mute say they do to reliability targets (F19 FR26, T5.7)", () => {
  it("says a snooze also holds back reliability target messages", () => {
    expect(quietEffect("snooze", "checkout")).toContain(
      "Also holds back reliability target messages.",
    );
  });

  it("says a mute stops findings only, and reliability target alerts continue", () => {
    const said = quietEffect("mute", "checkout");

    expect(said).toContain("Stops findings only. Reliability target alerts continue.");
    expect(said).not.toContain("Nothing is sent about checkout");
  });
});
