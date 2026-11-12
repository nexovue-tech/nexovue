import { describe, expect, it } from "vitest";

import type { Block, Comparison, Reason, Side } from "./api";
import {
  findingWindowSentence,
  measureLines,
  openingSentence,
  readingTone,
  readsAboutTheSame,
  warningSentences,
  windowWords,
} from "./comparison";

/** AC66: every method and reason asserts its exact opening sentence (design, *Opening sentence*). */

const NOW = Date.parse("2026-11-08T12:00:00Z");

function side(version: string, requests: number, errors: number, p95: number | null): Side {
  return { version, requests, errors, p95_ms: p95 };
}

function block(overrides: Partial<Block> = {}): Block {
  return {
    kind: "rollout",
    method: "side_by_side",
    from: "2026-11-08T11:00:00Z",
    to: "2026-11-08T11:42:00Z",
    minutes_compared: 42,
    this_version: side("v2.1", 6000, 180, 412),
    previous_version: side("v2.0", 6000, 12, 398),
    failure_share: { reading: "higher", note: null },
    p95: { reading: "about_same", note: null },
    previous_from: "2026-11-08T11:00:00Z",
    previous_to: "2026-11-08T11:42:00Z",
    last_week: null,
    ...overrides,
  };
}

function comparison(overrides: Partial<Comparison> = {}): Comparison {
  return {
    period_id: "p1",
    version: "v2.1",
    previous_version: "v2.0",
    reason: null,
    lead: "rollout",
    as_of: null,
    compared_to: "2026-11-08T11:42:00Z",
    window_hours: 1,
    blocks: [block()],
    traffic: null,
    other_versions: [],
    numbers_kept_days: 30,
    incomplete: false,
    minutes_differing: 0,
    versions_combined: false,
    mixed_environments: false,
    finding: null,
    ...overrides,
  };
}

const startedAt = "2026-11-08T11:00:00Z";

describe("the opening sentence", () => {
  it.each([
    [
      "side by side",
      comparison(),
      "Side by side over 42 minutes: failure share 3.0% on v2.1 against 0.2% on v2.0, higher. p95 412 ms against 398 ms, about the same.",
    ],
    [
      "side by side with mixed floors",
      comparison({
        blocks: [
          block({
            this_version: side("v2.1", 50, 1, null),
            previous_version: side("v2.0", 50, 0, null),
            failure_share: { reading: "not_clearly_different", note: null },
            p95: { reading: null, note: "too_little_traffic" },
          }),
        ],
      }),
      "Side by side over 42 minutes: failure share not clearly different. Too little traffic to read p95 (50 requests each).",
    ],
    [
      "side by side in warm-up",
      comparison({
        blocks: [
          block({
            minutes_compared: 3,
            failure_share: { reading: "not_clearly_different", note: null },
            p95: { reading: null, note: "warm_up" },
          }),
        ],
      }),
      "Side by side over 3 minutes: failure share not clearly different. p95 is left out for each version's first 5 minutes.",
    ],
    [
      "before and after, by version",
      comparison({
        lead: "after_rollout",
        blocks: [
          block({
            kind: "after_rollout",
            method: "version_before_after",
            minutes_compared: 60,
            this_version: side("v2.1", 10000, 40, 310),
            previous_version: side("v2.0", 10000, 30, 290),
            failure_share: { reading: "about_same", note: null },
            p95: { reading: "about_same", note: null },
          }),
        ],
      }),
      "Before and after, by version, over 1 hour: failure share 0.4% on v2.1 against 0.3% on v2.0, about the same. p95 310 ms against 290 ms, about the same. Different times of day can move these numbers.",
    ],
    [
      "before and after, by time",
      comparison({
        lead: "around_deploy",
        blocks: [
          block({
            kind: "around_deploy",
            method: "time_before_after",
            minutes_compared: 60,
            this_version: side("", 10000, 80, 300),
            previous_version: side("", 10000, 20, 290),
            failure_share: { reading: "higher", note: null },
          }),
        ],
      }),
      "Before and after, by time, over 1 hour: failure share 0.8% against 0.2%, higher. No version in your traffic, so a rolling deploy mixes old and new here.",
    ],
  ])("%s", (_, given, expected) => {
    expect(openingSentence(given, { startedAt, now: NOW })).toBe(expected);
  });

  it.each<[Reason, Partial<Comparison>, string]>([
    ["too_recent", {}, "Under 5 settled minutes so far. Refreshes on its own."],
    [
      "no_previous_version",
      {},
      "This is the first version we know of on this service, so there is nothing to compare it with.",
    ],
    [
      "already_running",
      {},
      "Running when we started counting. Comparisons start from the next rollout.",
    ],
    [
      "too_little_traffic",
      {
        traffic: {
          this_version: side("v2.1", 12, 0, null),
          previous_version: side("v2.0", 9, 0, null),
        },
      },
      "Too little traffic to compare: 12 requests on v2.1, 9 on v2.0.",
    ],
    [
      "numbers_expired",
      { numbers_kept_days: 30 },
      "Numbers for this release have expired (kept 30 days on this project).",
    ],
    [
      "no_version_no_deploy",
      {},
      "No version in your traffic, and no deploy reported, so we cannot compare.",
    ],
    [
      "new_version_not_seen",
      { other_versions: ["v2.0"] },
      "Reported, never seen in traffic. This service sends other versions (v2.0). The rollout may have failed, or the version text may differ.",
    ],
    [
      "no_request_traffic",
      {},
      "We count requests from server traces and queue consumers. This service sends none, so we can mark its deploys but cannot compare them. This is expected for a service that only runs in the browser.",
    ],
  ])("%s", (reason, extra, expected) => {
    const given = comparison({ reason, lead: null, blocks: [], ...extra });
    expect(openingSentence(given, { startedAt, now: NOW })).toBe(expected);
  });

  it("waits for traffic on a version reported under 30 minutes ago", () => {
    const given = comparison({
      reason: "new_version_not_seen",
      lead: null,
      blocks: [],
      other_versions: ["v2.0"],
    });
    expect(openingSentence(given, { startedAt: "2026-11-08T11:45:00Z", now: NOW })).toBe(
      "Waiting for traffic on v2.1.",
    );
  });

  it("names a long version by its start and end, never overflowing", () => {
    const long = `release-${"x".repeat(200)}-final`;
    const given = comparison({
      reason: "too_little_traffic",
      lead: null,
      blocks: [],
      traffic: { this_version: side(long, 1, 0, null), previous_version: side("v2.0", 2, 0, null) },
    });
    const sentence = openingSentence(given, { startedAt, now: NOW });
    expect(sentence).toContain("release-");
    expect(sentence).toContain("-final");
    expect(sentence.length).toBeLessThan(120);
  });
});

describe("each measure's line", () => {
  it("states each side's counts beside the plain word (phone layout)", () => {
    const [failure, p95, share] = measureLines(block());
    expect(failure).toEqual({
      label: "Failure share",
      reading: "higher",
      word: "higher",
      sides: ["v2.1 3.0% (180 of 6,000)", "v2.0 0.2% (12 of 6,000)"],
      lastWeek: null,
    });
    expect(p95?.sides).toEqual(["v2.1 412 ms", "v2.0 398 ms"]);
    expect(share).toEqual({
      label: "Share of traffic",
      reading: null,
      word: "",
      sides: ["v2.1 50.0%", "v2.0 50.0%"],
      lastWeek: null,
    });
  });

  it("shows requests a minute, not share, before and after (FR25)", () => {
    const lines = measureLines(block({ kind: "after_rollout", method: "version_before_after" }));
    expect(lines[2]?.label).toBe("Requests a minute");
    expect(lines[2]?.sides).toEqual(["v2.1 142.9", "v2.0 142.9"]);
  });

  it("gives higher the critical text and every other word dim ink, never green (FR24)", () => {
    expect(readingTone("higher")).toBe("text-critical-text");
    for (const reading of ["lower", "about_same", "not_clearly_different", null] as const) {
      expect(readingTone(reading)).toBe("text-ink-dim");
    }
  });
});

describe("a comparison opened from a finding (FR22, AC64)", () => {
  it("says the window the API chose reaches the finding", () => {
    expect(findingWindowSentence(6, true, "14:00 UTC")).toBe(
      "Compared over 6 hours, to include the finding that started at 14:00 UTC.",
    );
  });

  it("says a comparison ending before the finding says nothing about it", () => {
    expect(findingWindowSentence(24, false, "15:00 UTC")).toBe(
      "This comparison ends before that finding started, so it says nothing about it.",
    );
  });

  it("without a finding, each window reads its own", () => {
    expect([1, 6, 24].map((hours) => windowWords(hours))).toEqual([
      "Compared over 1 hour from the start.",
      "Compared over 6 hours from the start.",
      "Compared over 24 hours from the start.",
    ]);
  });
});

describe("what the numbers cannot hide (FR17, FR28, FR45)", () => {
  it("states each true warning, and none when nothing is wrong", () => {
    expect(warningSentences(comparison())).toEqual([]);
    expect(
      warningSentences(
        comparison({
          mixed_environments: true,
          versions_combined: true,
          incomplete: true,
          minutes_differing: 1,
        }),
      ),
    ).toEqual([
      "Two environments appear to share this project, so these comparisons mix them.",
      "Versions were combined: too many in one minute, so a version's count may be short.",
      "Incomplete: some counts were not stored for 1 minute.",
    ]);
  });

  it("puts FR49's caution under about the same only (AC79)", () => {
    expect(readsAboutTheSame(block())).toBe(true);
    expect(
      readsAboutTheSame(
        block({
          failure_share: { reading: "higher", note: null },
          p95: { reading: "lower", note: null },
        }),
      ),
    ).toBe(false);
  });

  it("gives last week a column in before-and-after methods only (FR25, AC65)", () => {
    const after = block({
      kind: "after_rollout",
      method: "version_before_after",
      last_week: side("", 4000, 12, 300),
    });
    expect(measureLines(after).map((line) => line.lastWeek)).toEqual([
      "0.3% (12 of 4,000)",
      "300 ms",
      "95.2",
    ]);
    expect(measureLines(block()).map((line) => line.lastWeek)).toEqual([null, null, null]);
    expect(measureLines(block()).map((line) => line.label)).toContain("Share of traffic");
  });
});
