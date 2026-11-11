// @vitest-environment jsdom

import { act } from "react";
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
          points: [],
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

// ------------------------------------------------------- the case filters (F22 T2.4)

const ACTIVE_CASE = finding({
  id: "c-1",
  state: "closed",
  closed_reason: "recovered",
  closed_at: "2026-11-02T15:04:00Z",
  case_state: "investigating",
  case_signal: "closed",
  assignee_id: "someone",
  assignee_name: "Asha",
});

function feedReads(extra: Record<string, ReturnType<typeof json>> = {}) {
  return stubApi(finding(), {
    extra: {
      "GET /findings": json({
        findings: [{ ...ACTIVE_CASE, project_name: "Staging" }],
        muted_count: 0,
        truncated: false,
      }),
      // AC109: the telemetry summary and detection's status fail outright.
      "GET /services/overview": json({ detail: "down" }, 503),
      "GET /synthetics/checks": json({ detail: "down" }, 503),
      [`GET /projects/${PROJECT_ID}/detection/status`]: json({ detail: "down" }, 503),
      ...extra,
    },
  });
}

describe("a case filter is its own read (AC109, FR22)", () => {
  it("shows the cases when the summary and detection fail, and reads no project", async () => {
    const api = feedReads();

    const { container } = await mountFeed("?case=active");

    await waitFor(() => readable(container).includes("Staging"), "the case row");
    const text = readable(container);
    expect(text).not.toContain("could not be loaded");
    expect(text).toContain("Across all your projects");
    const reads = api.to("GET", "/findings");
    expect(reads.length).toBeGreaterThan(0);
    for (const read of reads) {
      expect(new URLSearchParams(read.search).get("case")).toBe("active");
      expect(new URLSearchParams(read.search).has("project_id")).toBe(false);
    }
    // It waits on nothing else: neither the summary nor detection was asked.
    expect(api.to("GET", "/services/overview")).toEqual([]);
    expect(api.to("GET", `/projects/${PROJECT_ID}/detection/status`)).toEqual([]);
  });

  it("lives in the address: choosing a filter changes it, and each row carries it (AC28, AC128)", async () => {
    feedReads();
    const { container, router } = await mountFeed("?case=active");
    await waitFor(() => readable(container).includes("Staging"), "the case row");

    const link = container.querySelector<HTMLAnchorElement>('a[href^="/findings/c-1"]');
    expect(link?.getAttribute("href")).toBe("/findings/c-1?case=active");

    const select = container.querySelector<HTMLSelectElement>("select");
    expect(select?.value).toBe("active");
    await act(async () => {
      if (select) {
        select.value = "mine";
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    await waitFor(() => router.state.location.search.case === "mine", "the address to change");
  });
});
