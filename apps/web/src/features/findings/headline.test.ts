import { describe, expect, it } from "vitest";

import type { CheckCounts, ProjectTotals } from "./api";
import { headlineFor } from "./headline";

/**
 * The defect this guards (`docs/changes/findings-false-all-clear/bug.md`): the screen
 * printed "All systems normal" with no engine behind it, over checks that were failing
 * and then over errors nothing reads. An empty feed is not evidence of health.
 */

const TOTALS: ProjectTotals = {
  service_count: 3,
  error_count: 0,
  requests_per_minute: 900,
  error_rate: 0,
  p95_ms: 120,
};

const checks = (over: Partial<CheckCounts> = {}): CheckCounts => ({
  total: 4,
  passing: 4,
  down: 0,
  ...over,
});

describe("what the screen may claim when the feed is empty", () => {
  it("never claims the whole system is healthy, because nothing has assessed it", () => {
    // No engine reads errors, latency or traffic yet. A project failing nine calls
    // in ten has passing checks and an empty feed, and used to read "normal".
    const failing = { ...TOTALS, error_count: 8100, error_rate: 0.9, p95_ms: 30_000 };

    for (const headline of [
      headlineFor(failing, checks()),
      headlineFor(failing, checks({ total: 0, passing: 0 })),
      headlineFor(failing, undefined),
      headlineFor(TOTALS, checks()),
    ]) {
      expect(headline.title).not.toMatch(/normal|healthy|all good|no problems/i);
    }
  });

  it("says what it actually checked when every check passes", () => {
    expect(headlineFor(TOTALS, checks())).toEqual({
      title: "All 4 synthetic checks are passing",
      wrong: false,
    });
  });

  it("reads as a sentence with one check", () => {
    expect(headlineFor(TOTALS, checks({ total: 1, passing: 1 })).title).toBe(
      "The synthetic check is passing",
    );
  });

  it("names the failing checks", () => {
    const headline = headlineFor(TOTALS, checks({ passing: 0, down: 4 }));

    expect(headline.title).toBe("4 checks are down");
    expect(headline.wrong).toBe(true);
  });

  it("reads as a sentence when only one is down", () => {
    expect(headlineFor(TOTALS, checks({ passing: 3, down: 1 })).title).toBe("A check is down");
  });

  it("does not call a check down, or passing, when it simply has not run", () => {
    // `up` is null for a check with no run in this window. That is a gap in what
    // we can see, so the sentence covers only what the probes reported.
    expect(headlineFor(TOTALS, checks({ passing: 2, down: 0 }))).toEqual({
      title: "2 of 4 synthetic checks are passing, and the rest have not run",
      wrong: false,
    });
  });

  it("says so when no check has run at all", () => {
    expect(headlineFor(TOTALS, checks({ passing: 0, down: 0 }))).toEqual({
      title: "No synthetic check has run in this window",
      wrong: false,
    });
  });

  it("promises nothing when there are no checks, or they have not been read", () => {
    // Absent is not "none down", and it is not a reason to alarm anyone either.
    for (const unread of [undefined, checks({ total: 0, passing: 0 })]) {
      expect(headlineFor(TOTALS, unread)).toEqual({
        title: "Here is what this project reported",
        wrong: false,
      });
    }
  });

  it("claims nothing at all when nothing has reported", () => {
    expect(headlineFor(undefined, undefined)).toEqual({
      title: "Nothing has reported to this project yet",
      wrong: false,
    });
  });
});
