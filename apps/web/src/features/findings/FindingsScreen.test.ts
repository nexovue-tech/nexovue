// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";

import type { DetectionStatus } from "./api";
import {
  finding,
  json,
  mountFeed,
  PROJECT_ID,
  readable,
  stubApi,
  unmountAll,
  waitFor,
} from "./render-harness.test-util";

/**
 * The feed as the app mounts it: what its reads add up to, apart from how each is drawn
 * (`FindingsView.test.ts`).
 */

afterEach(unmountAll);

const NOT_RUNNING: DetectionStatus = {
  state: "not_running",
  services_total: 2,
  services_learning: 0,
  services_judged: 2,
  services_delayed: 0,
  services_muted: 0,
  earliest_judging_date: null,
  not_running_since: "2026-10-31T14:02:00Z",
  delayed_since: null,
  open_findings: 0,
  services_too_many: 0,
  judged_service_limit: 500,
};

describe("the findings feed", () => {
  it("reads its times on the project's clock, not the browser's", async () => {
    stubApi(finding(), {
      extra: {
        "GET /findings": json({ findings: [], muted_count: 0, truncated: false }),
        "GET /services/overview": json({
          totals: {
            service_count: 2,
            error_count: 0,
            requests_per_minute: 10,
            error_rate: 0,
            p95_ms: 100,
          },
        }),
        "GET /synthetics/checks": json({ checks: [] }),
        [`GET /projects/${PROJECT_ID}/detection/status`]: json(NOT_RUNNING),
        [`GET /projects/${PROJECT_ID}/detection`]: json({
          sensitivity: "normal",
          timezone: "America/New_York",
        }),
      },
    });
    const { container } = await mountFeed();

    await waitFor(() => readable(container).includes("Detection has not run since"), "the line");
    // 14:02 UTC is 10:02 in New York; the browser here reads UTC.
    await waitFor(() => readable(container).includes("10:02"), "the project's clock");
    expect(readable(container)).not.toContain("14:02");
  });
});
