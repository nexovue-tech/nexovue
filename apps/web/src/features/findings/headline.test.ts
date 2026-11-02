import { describe, expect, it } from "vitest";

import type { CheckCounts, DetectionStatus, ProjectTotals } from "./api";
import { detectionHeadlineFor, headlineFor } from "./headline";

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

/**
 * The detection engine's line, above the checks' (FR25, AC42, AC26). It may say
 * "normal" now, because something judged it, but only about what was judged.
 */
const status = (over: Partial<DetectionStatus> = {}): DetectionStatus => ({
  state: "judging",
  services_total: 5,
  services_learning: 0,
  services_judged: 5,
  services_delayed: 0,
  services_muted: 0,
  earliest_judging_date: null,
  not_running_since: null,
  delayed_since: null,
  open_findings: 0,
  services_too_many: 0,
  judged_service_limit: 500,
  ...over,
});

// 14:02 in London on the day of the reading, and the reader's now an hour later.
const SINCE = "2026-10-31T14:02:00Z";
const AT = { zone: "Europe/London", now: Date.parse("2026-10-31T15:02:00Z") };

describe("what detection may say above the checks", () => {
  it("says it is learning, and when judging starts, while every service learns (AC42)", () => {
    const headline = detectionHeadlineFor(
      status({
        state: "learning",
        services_learning: 5,
        services_judged: 0,
        earliest_judging_date: "2026-11-03",
      }),
      AT,
    );

    expect(headline).toEqual({
      title: "Learning your services' normal. Judging starts on 3 Nov",
      wrong: false,
    });
    expect(headline?.title).not.toMatch(/all .*normal/i);
  });

  it("does not promise a start day it was not given", () => {
    const headline = detectionHeadlineFor(
      status({ state: "learning", services_learning: 5, services_judged: 0 }),
      AT,
    );

    expect(headline?.title).toBe("Learning your services' normal");
  });

  it("speaks only for the judged services while some still learn", () => {
    expect(detectionHeadlineFor(status({ services_learning: 2, services_judged: 3 }), AT)).toEqual({
      title: "All judged services are normal. 2 are still learning",
      wrong: false,
    });
    expect(
      detectionHeadlineFor(status({ services_learning: 1, services_judged: 4 }), AT)?.title,
    ).toBe("All judged services are normal. 1 is still learning");
  });

  it("says how many services' data is delayed, never that every judged one is normal", () => {
    expect(detectionHeadlineFor(status({ services_delayed: 2 }), AT)).toEqual({
      title: "Data from 2 services is delayed, so they are judged once it arrives",
      wrong: false,
    });
    expect(detectionHeadlineFor(status({ services_delayed: 1 }), AT)?.title).toBe(
      "Data from 1 service is delayed, so it is judged once it arrives",
    );
  });

  it("says so while a finding is open too: the feed cannot speak for those services", () => {
    expect(detectionHeadlineFor(status({ services_delayed: 2, open_findings: 1 }), AT)?.title).toBe(
      "Data from 2 services is delayed, so they are judged once it arrives",
    );
  });

  it("calls the judged services normal when every one is judged and nothing is open", () => {
    expect(detectionHeadlineFor(status(), AT)).toEqual({
      title: "All judged services are normal",
      wrong: false,
    });
  });

  it("calls nothing normal while a finding is open: the feed leads", () => {
    for (const open of [
      status({ open_findings: 1 }),
      status({ services_learning: 2, open_findings: 3 }),
    ]) {
      expect(detectionHeadlineFor(open, AT)).toBeUndefined();
    }
  });

  it("says it is behind on the data, and since when, without alarming anyone", () => {
    expect(
      detectionHeadlineFor(status({ state: "data_delayed", delayed_since: SINCE }), AT),
    ).toEqual({
      title:
        "nexovue is behind on reading your data since 14:02 London time — your services may be fine",
      wrong: false,
    });
  });

  it("says detection is not running, and since when, and never that anything is normal (AC26)", () => {
    const headline = detectionHeadlineFor(
      status({ state: "not_running", not_running_since: SINCE }),
      AT,
    );

    expect(headline).toEqual({
      title: "Detection has not run since 14:02 London time",
      wrong: true,
    });
  });

  it("drops the time, not the sentence, when it was not told when", () => {
    expect(detectionHeadlineFor(status({ state: "not_running" }), AT)?.title).toBe(
      "Detection is not running",
    );
    expect(
      detectionHeadlineFor(status({ state: "data_delayed", delayed_since: "garbled" }), AT)?.title,
    ).toBe("nexovue is behind on reading your data — your services may be fine");
  });

  it("says how many services are past the cap, and that the busiest are judged", () => {
    const headline = detectionHeadlineFor(
      status({ state: "too_many_services", services_judged: 500, services_too_many: 37 }),
      AT,
    );

    expect(headline).toEqual({
      title: "Too many services to judge all: the 500 busiest are judged, and 37 are not",
      wrong: false,
    });
    expect(headline?.title).not.toMatch(/normal/i);
  });

  it("says so while a finding is open too, because the feed cannot speak for the rest", () => {
    expect(
      detectionHeadlineFor(
        status({ state: "too_many_services", services_too_many: 1, open_findings: 2 }),
        AT,
      )?.title,
    ).toBe("Too many services to judge all: the 500 busiest are judged, and 1 is not");
  });

  it("says nothing when the project has no services to judge", () => {
    expect(
      detectionHeadlineFor(
        status({ state: "no_services", services_total: 0, services_judged: 0 }),
        AT,
      ),
    ).toBeUndefined();
  });

  it("says nothing healthy when the status could not be read", () => {
    // The screen shows its error state for a failed read (`FindingsScreen.tsx`);
    // here, absent must never fall through to a healthy line.
    expect(detectionHeadlineFor(undefined, AT)).toBeUndefined();
  });
});
