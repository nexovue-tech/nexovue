import { describe, expect, it } from "vitest";

import type { ServiceDetection, SignalStatus } from "./api";
import { describeBadge } from "./badge";

/**
 * A service's detection badge (FR25, BR4): every state says what is true, and none
 * that is not judging may read as healthy.
 */

const judged = (signal: SignalStatus["signal"]): SignalStatus => ({
  signal,
  state: "judged",
  learning_until: null,
  reason: null,
});

function detection(overrides: Partial<ServiceDetection> = {}): ServiceDetection {
  return {
    state: "judged",
    signals: [judged("slower"), judged("failing"), judged("traffic")],
    muted_until: null,
    snoozed_until: null,
    findings: [],
    truncated: false,
    ...overrides,
  };
}

describe("the detection badge", () => {
  it("says a read that failed, and never Judged, for a service it could not read", () => {
    const badge = describeBadge(undefined, { failed: true });

    expect(badge?.label).toBe("Detection unavailable");
    expect(badge?.tone).toBe("critical");
  });

  it("says nothing while the read is under way", () => {
    expect(describeBadge(undefined, { failed: false })).toBeNull();
  });

  it.each([
    ["not_running", "Detection not running"],
    ["data_delayed", "Data delayed"],
    ["no_detection_yet", "No detection yet"],
    ["muted", "Muted"],
  ] as const)("names %s as %s", (state, label) => {
    expect(describeBadge(detection({ state }), { failed: false })?.label).toBe(label);
  });

  it("names the day a learning service is judged from: its latest signal's", () => {
    const badge = describeBadge(
      detection({
        state: "learning",
        signals: [
          { ...judged("slower"), state: "learning", learning_until: "2026-11-03" },
          { ...judged("traffic"), state: "learning", learning_until: "2026-11-21" },
        ],
      }),
      { failed: false },
    );

    expect(badge?.label).toBe("Learning until 21 Nov");
  });

  it("says which signal a judged service is too quiet to judge", () => {
    const badge = describeBadge(
      detection({
        signals: [judged("slower"), { ...judged("traffic"), state: "too_little_traffic" }],
      }),
      { failed: false },
    );

    expect(badge?.label).toBe("Judged");
    expect(badge?.detail).toBe("Too little traffic to judge traffic.");
  });

  it("says until when a muted service is muted, on the project's clock", () => {
    const badge = describeBadge(
      detection({ state: "muted", muted_until: "2026-11-04T18:30:00Z" }),
      { failed: false, zone: "UTC" },
    );

    expect(badge?.detail).toContain("until 4 Nov, 18:30 UTC");
  });
});
