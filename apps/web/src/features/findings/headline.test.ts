import { describe, expect, it } from "vitest";

import type { CheckCounts, ProjectTotals } from "./api";
import { headlineFor } from "./headline";

/**
 * The defect this guards (`docs/quality/console-review-2026-09-20.md`, D1): the
 * screen printed "All systems normal" above its own evidence that four of the
 * project's four checks were failing. An empty feed is not evidence of health.
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
  it("says everything is normal only when nothing it can see is failing", () => {
    expect(headlineFor(TOTALS, checks())).toEqual({ title: "All systems normal", wrong: false });
  });

  it("names the failing checks instead of claiming health over the top of them", () => {
    const headline = headlineFor(TOTALS, checks({ passing: 0, down: 4 }));

    expect(headline.title).toBe("4 checks are down");
    expect(headline.wrong).toBe(true);
  });

  it("reads as a sentence when only one is down", () => {
    expect(headlineFor(TOTALS, checks({ passing: 3, down: 1 })).title).toBe("A check is down");
  });

  it("does not call a check down when it simply has not run", () => {
    // `up` is null for a check with no run in this window. That is a gap in what
    // we can see, and saying "down" would invent a failure the probe never found.
    const silent = headlineFor(TOTALS, checks({ passing: 2, down: 0 }));

    expect(silent).toEqual({ title: "All systems normal", wrong: false });
  });

  it("claims nothing at all when nothing has reported", () => {
    expect(headlineFor(undefined, undefined)).toEqual({
      title: "Nothing has reported to this project yet",
      wrong: false,
    });
  });

  it("claims nothing when the checks have not been read", () => {
    // The checks query waits for the summary and can still be in flight. Absent
    // is not "none down" — but it is also not a reason to alarm anyone.
    expect(headlineFor(TOTALS, undefined).wrong).toBe(false);
  });
});
