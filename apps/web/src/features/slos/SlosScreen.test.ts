// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SloView } from "./api";
import {
  json,
  list,
  minutesAgo,
  mountAt,
  PROJECT_ID,
  readable,
  stubApi,
  target,
  unmountAll,
  waitFor,
} from "./slo-harness.test-util";

const PROJECTS = json({
  projects: [{ id: PROJECT_ID, slug: "production", name: "production", first_reported_on: null }],
});

function listed(targets: SloView[], extra: Parameters<typeof list>[1] = {}) {
  return { "GET /projects": PROJECTS, "GET /slos": json(list(targets, extra)) };
}

afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

describe("the targets list", () => {
  it("reads 'Failures still allowed: N of M' for a target on track (Step 1's judgeable)", async () => {
    stubApi(listed([target()]));
    const { container } = await mountAt("/slos");
    await waitFor(
      () => readable(container).includes("Failures still allowed: 360 of 672."),
      "the figure",
    );
    expect(readable(container)).toContain("0 of 1 need attention.");
  });

  it("says No recent requests for a stopped service and counts it (AC8 on screen)", async () => {
    const quiet = target({
      state: "no_recent_requests",
      attention: true,
      quiet_minutes: 190,
      last_event_minute: minutesAgo(190),
    });
    stubApi(listed([quiet]));
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("No requests for 3 h 10 m"), "the silence");
    expect(readable(container)).toContain("1 of 1 need attention.");
    expect(readable(container)).not.toContain("On track");
  });

  it("puts the counted rows first, so the top N are the headline's N (AC110)", async () => {
    const fine = target({ id: "fine", name: "fine" });
    const missed = target({
      id: "missed",
      name: "missed",
      state: "nothing_left",
      variant: "missed",
      attention: true,
    });
    stubApi(listed([fine, missed]));
    const { container } = await mountAt("/slos");
    await waitFor(() => container.querySelector("li a"), "the rows");
    const rows = [...container.querySelectorAll("li a")].map((row) => readable(row));
    expect(rows[0]).toContain("missed");
    expect(readable(container)).toContain("1 of 2 need attention.");
  });

  it("explains a target and offers one, with no suggestion read, when there is none (AC41)", async () => {
    const calls = stubApi(listed([]));
    const { container } = await mountAt("/slos");
    await waitFor(
      () => readable(container).includes("No reliability targets yet"),
      "the empty state",
    );
    expect(readable(container)).toContain("New target");
    expect(calls.some((call) => call.path.startsWith("/slo-readings"))).toBe(false);
  });

  it("says nothing is reporting yet when the project has no service (AC41)", async () => {
    stubApi(listed([], { has_services: false }));
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("Nothing is reporting yet"), "no services");
  });

  it("tells a viewer who can add one and shows no New target (AC41, AC83)", async () => {
    stubApi(listed([]), "viewer");
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("Ask an owner, admin or member"), "who may");
    expect(readable(container)).not.toContain("New target");
  });

  it("shows a failure with a retry, and no headline or empty state, when it cannot load (AC82)", async () => {
    stubApi({
      "GET /projects": PROJECTS,
      "GET /slos": json({ type: "about:blank", title: "Unavailable", status: 503 }, 503),
    });
    const { container } = await mountAt("/slos");
    await waitFor(
      () => readable(container).includes("The targets could not be loaded"),
      "the failure",
    );
    expect(readable(container)).not.toContain("need attention");
    expect(readable(container)).not.toContain("No reliability targets yet");
  });

  it("never says On track on stale status, and a banner says how many (AC63)", async () => {
    stubApi(listed([target({ evaluated_minute: minutesAgo(20) }), target({ id: "two" })]));
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("not up to date for 1 of 2"), "the banner");
    expect(readable(container)).toContain("Not up to date since");
  });

  it("stops showing On track after 15 minutes when every refresh fails (AC107)", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    stubApi(listed([target()]));
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("Failures still allowed"), "fresh figures");
    stubApi({
      "GET /projects": PROJECTS,
      "GET /slos": json({ type: "about:blank", title: "Unavailable", status: 503 }, 503),
    });
    vi.setSystemTime(Date.now() + 16 * 60_000);
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    await waitFor(() => readable(container).includes("Not up to date since"), "the stale sentence");
    expect(readable(container)).not.toContain("Failures still allowed: 360 of 672.");
  });

  it("shows a deleted target's line with Restore for a member (FR31, AC71)", async () => {
    stubApi(
      listed([], {
        deleted: [
          {
            id: "gone",
            name: "checkout availability",
            label: "checkout availability, 99.9% over 28 days",
            deleted_at: "2026-11-03T10:00:00Z",
            purged_on: "2026-12-03",
            days_held: 12,
            deleted_by_name: "Priya",
          },
        ],
      }),
      "member",
    );
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("was deleted by Priya on 3 Nov"), "the line");
    expect(readable(container)).toContain("Its daily figures go on 3 Dec.");
    expect(readable(container)).toContain("Restore");
  });
});
