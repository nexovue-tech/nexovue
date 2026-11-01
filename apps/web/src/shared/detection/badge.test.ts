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

  it("says a service past the project's cap is not judged, and never Judged or delayed", () => {
    const badge = describeBadge(detection({ state: "too_many_services" }), { failed: false });

    expect(badge?.label).toBe("Too many services");
    expect(badge?.detail).toBe(
      "This project has more services than detection judges. Only the busiest are judged, and this is not one of them.",
    );
    expect(badge?.tone).toBe("attention");
  });

  it("says a quiet service is not judged, and why, rather than calling it judged", () => {
    const badge = describeBadge(detection({ state: "no_incoming_requests" }), { failed: false });

    expect(badge?.label).toBe("Not judged: no incoming requests");
    expect(badge?.detail).toBe(
      "None arrived in the last window, and its normal expects too few for that to be a problem.",
    );
  });

  it("says detection judges incoming requests when it has judged none of a service's", () => {
    // A service with only outgoing or internal calls never has any (design, *Inputs*).
    expect(describeBadge(detection({ state: "no_detection_yet" }), { failed: false })?.detail).toBe(
      "Detection judges a service's incoming requests, and has judged none of this one's yet.",
    );
  });

  it("leads with the day judging starts, as the feed does, and names traffic's later day", () => {
    // Spec, *Error cases*: "Learning, with the date judging starts. Traffic drops wait
    // for 3 weeks."
    const badge = describeBadge(
      detection({
        state: "learning",
        signals: [
          { ...judged("slower"), state: "learning", learning_until: "2026-11-03" },
          { ...judged("failing"), state: "learning", learning_until: "2026-11-03" },
          { ...judged("traffic"), state: "learning", learning_until: "2026-11-21" },
        ],
      }),
      { failed: false },
    );

    expect(badge?.label).toBe("Learning until 3 Nov");
    expect(badge?.detail).toBe("Traffic drops are judged from 21 Nov.");
  });

  it("adds nothing when every signal starts on the same day", () => {
    const badge = describeBadge(
      detection({
        state: "learning",
        signals: [
          { ...judged("slower"), state: "learning", learning_until: "2026-11-03" },
          { ...judged("traffic"), state: "learning", learning_until: "2026-11-03" },
        ],
      }),
      { failed: false },
    );

    expect(badge?.label).toBe("Learning until 3 Nov");
    expect(badge?.detail).toBeNull();
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
