// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SloView } from "./api";
import {
  AUDIENCE,
  buttons,
  json,
  list,
  minutesAgo,
  monthRow,
  mountAt,
  PROJECT_ID,
  press,
  REQUEST_LABEL,
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
  it("puts a No alerts or Muted chip on a row whose burns go nowhere (FR25a, AC84)", async () => {
    const silent = target({
      id: "slo-2",
      name: "cart availability",
      told: { alerting: false, rules: [], snoozed_until: null, snoozed_by_name: null },
    });
    const rule = target().told.rules[0];
    if (rule === undefined) throw new Error("the harness's default rule is missing");
    const muted = target({
      id: "slo-3",
      name: "ad availability",
      told: {
        alerting: false,
        rules: [{ ...rule, muted_until: new Date(Date.now() + 3_600_000).toISOString() }],
        snoozed_until: null,
        snoozed_by_name: null,
      },
    });
    stubApi(listed([target(), silent, muted]));
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("cart availability"), "the rows");
    const rows = [...container.querySelectorAll("li")].map((row) => row.textContent ?? "");
    expect(rows.find((row) => row.includes("cart availability"))).toContain("No alerts");
    expect(rows.find((row) => row.includes("ad availability"))).toContain("Muted");
    expect(rows.find((row) => row.includes("checkout availability"))).not.toContain("No alerts");
  });

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

describe("last month (Step 3)", () => {
  function lastMonth() {
    return {
      month: "2026-10-01",
      zone: "UTC",
      audience: AUDIENCE,
      targets: 4,
      met: 2,
      missed: 1,
      not_judged: 1,
      met_with_time_not_counted: 1,
      rows: [
        monthRow({
          id: "pay",
          name: null,
          label: "payment availability, 99.9% over 28 days",
          result: "missed",
          figure_percent: "99.80",
          deleted_at: "2026-11-03T10:00:00Z",
        }),
        monthRow({
          id: "cart",
          name: "cart",
          label: "cart availability, 99.9% over 28 days",
          result: "not_judged",
          coverage: 0.71,
        }),
        monthRow({ excluded_minutes: 120, time_not_counted: true }),
        monthRow({ id: "ad", name: "ad", label: "ad availability, 99.9% over 28 days" }),
      ],
    };
  }

  it("heads 'N of M targets met' with missed and not judged named, and no single figure (AC78, AC112)", async () => {
    stubApi({ ...listed([target()]), "GET /slos/months": json(lastMonth()) });
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("Last month"), "the views");
    await press(container, "Last month");
    await waitFor(() => readable(container).includes("targets met in October"), "the headline");
    expect(readable(container)).toContain(
      "2 of 4 targets met in October. 1 missed. 1 not judged. 1 of those met has time not counted.",
    );
    expect(readable(container)).toContain("There is no single figure across targets");
    expect(readable(container)).toContain("A project is one environment");
    expect(readable(container)).toContain("Deleted on 3 Nov.");
    expect(readable(container)).toContain("2 h left out.");
    expect(readable(container)).toContain(AUDIENCE);
    const download = container.querySelector('a[href*="slos/months.csv"]');
    expect(download?.getAttribute("href")).toContain(`project_id=${PROJECT_ID}`);
  });

  it("copies a row with its period, quiet and excluded time and fixed label (AC78)", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const only = {
      ...lastMonth(),
      targets: 1,
      met: 1,
      missed: 0,
      not_judged: 0,
      rows: [monthRow()],
    };
    stubApi({ ...listed([target()]), "GET /slos/months": json(only) });
    const { container } = await mountAt("/slos");
    await waitFor(() => readable(container).includes("Last month"), "the views");
    await press(container, "Last month");
    await waitFor(() => buttons(container).includes("Copy"), "the copy action");
    await press(container, "Copy");
    const copied = String(writeText.mock.calls[0]?.at(0));
    expect(copied).toContain("October 2026: 99.95% against 99.9%. Measured 100% of the month.");
    expect(copied).toContain("No minute without requests.");
    expect(copied).toContain("Nothing left out for planned work.");
    expect(copied).toContain(REQUEST_LABEL);
  });
});
