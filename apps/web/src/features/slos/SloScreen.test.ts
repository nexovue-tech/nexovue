// @vitest-environment jsdom

import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AUDIENCE,
  buttons,
  json,
  minutesAgo,
  month,
  monthsOf,
  mountAt,
  press,
  REQUEST_LABEL,
  readable,
  stubApi,
  target,
  unmountAll,
  waitFor,
} from "./slo-harness.test-util";

afterEach(unmountAll);

// The chart draws on a canvas jsdom has not got: what the page hands it is what is
// tested here, and `TrendChart`'s own tests hold how it draws.
vi.mock("@/shared/components/TrendChart", () => ({
  CHART_HEIGHT: 132,
  TrendChart: (props: { label: readonly string[]; data: unknown[][]; description: string }) =>
    createElement("div", {
      role: "img",
      "aria-label": props.description,
      "data-labels": props.label.join("|"),
      "data-points": JSON.stringify(props.data[1]),
    }),
}));

function burnLine(points: Array<number | null>, tier: string | null = "fast") {
  const last = Date.now() - 60_000;
  return {
    tier,
    evaluated_minute: new Date(last).toISOString(),
    on_pace: 1,
    points: points.map((rate, index) => ({
      minute: new Date(last - (points.length - 1 - index) * 60_000).toISOString(),
      rate,
    })),
  };
}

const minutesFromNow = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();

/** A critical burn the job stored: 14.7 times faster, 50 hours left (Step 4). */
const BURN = {
  severity: "critical" as const,
  rate: 14.7,
  since: minutesAgo(20),
  hours_left: 50,
  resolving: false,
  judged: true,
  last_held_minute: minutesAgo(1),
  chart_tier: "fast",
};

describe("one target", () => {
  it("leads with its sentence, then what was measured, as of a time in the project's zone (AC108)", async () => {
    const evaluated = new Date();
    evaluated.setUTCSeconds(0, 0);
    stubApi({
      "GET /slos/slo-1": json(
        target({ zone: "Asia/Kolkata", evaluated_minute: evaluated.toISOString() }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(container).includes("Failures still allowed: 360 of 672."),
      "the sentence",
    );
    const clock = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Kolkata",
    }).format(evaluated);
    expect(readable(container)).toContain(`As of ${clock} (IST)`);
    const text = readable(container);
    expect(text.indexOf("Failures still allowed: 360")).toBeLessThan(
      text.indexOf("What was measured"),
    );
  });

  it("states how many days of figures go, and when, before a delete can be confirmed (AC42)", async () => {
    stubApi(
      { "GET /slos/slo-1": json(target({ figures: { ...target().figures, days_held: 12 } })) },
      "member",
    );
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => buttons(container).includes("Delete"), "the delete action");
    await press(container, "Delete");
    expect(readable(container)).toContain("Its 12 days of figures go on");
    expect(readable(container)).toContain(
      "It can be restored until then. Past months' results stay.",
    );
  });

  it("shows a viewer no Edit, Delete or Disable (AC83)", async () => {
    stubApi({ "GET /slos/slo-1": json(target()) }, "viewer");
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("Failures still allowed"), "the page");
    expect(readable(container)).not.toContain("Edit");
    expect(buttons(container)).not.toContain("Delete");
    expect(buttons(container)).not.toContain("Disable");
  });

  it("refuses a viewer who follows an edit link (AC83)", async () => {
    stubApi({ "GET /slos/slo-1": json(target()) }, "viewer");
    const { container } = await mountAt("/slos/slo-1/edit");
    await waitFor(
      () => readable(container).includes("an owner, an admin or a member"),
      "the refusal",
    );
    expect(container.querySelector("form")).toBeNull();
  });

  it("names a burn on stale status 'Was using…' with no live sentence (AC51, AC63)", async () => {
    stubApi({
      "GET /slos/slo-1": json(
        target({
          state: "running_out",
          severity: "critical",
          attention: true,
          evaluated_minute: minutesAgo(20),
          burn: BURN,
        }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(
      () =>
        readable(container).includes(
          "Was using allowed failures 14 times faster than allowed when last worked out at",
        ),
      "the stale burn",
    );
    expect(readable(container)).not.toContain("What is left lasts");
    expect(readable(container)).not.toContain("Running out, act now");
  });

  it("says how fast a burn runs, since when, how long what is left lasts, and what to do (FR10)", async () => {
    stubApi({
      "GET /slos/slo-1": json(
        target({ state: "running_out", severity: "critical", attention: true, burn: BURN }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(container).includes("Using allowed failures 14 times faster than allowed"),
      "the burn",
    );
    expect(readable(container)).toContain("What is left lasts about 2 days.");
    expect(readable(container)).toContain("Open the incident.");
  });

  it("speaks of slow requests on a latency target's burn (AC89)", async () => {
    stubApi({
      "GET /slos/slo-1": json(
        target({
          kind: "service_latency",
          state: "running_out",
          severity: "warning",
          attention: true,
          burn: { ...BURN, severity: "warning" },
        }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("allowed slow requests"), "the burn");
    expect(readable(container)).toContain("Can wait for working hours.");
  });

  it("draws how fast against the on-pace line, with gaps, and says the current and peak (AC109)", async () => {
    stubApi({
      "GET /slos/slo-1": json(
        target({ state: "running_out", severity: "critical", attention: true, burn: BURN }),
      ),
      "GET /slo-readings/slo-1/burn": json(burnLine([2, null, 20.4, 14.7])),
    });
    const { container } = await mountAt("/slos/slo-1");
    const chart = await waitFor(
      () => container.querySelector<HTMLElement>('[role="img"][data-labels]'),
      "the chart",
    );
    expect(chart.dataset.labels).toBe("Times faster than allowed|On pace (1)");
    expect(JSON.parse(chart.dataset.points ?? "[]")).toEqual([2, null, 20.4, 14.7]);
    expect(chart.getAttribute("aria-label")).toBe(
      "Times faster than allowed over the last 24 hours, the fast tier: now 14.7, peak 20.4. On pace is 1.",
    );
  });

  it("says when no tier can judge, and keeps the figures when the chart fails (AC109, AC82)", async () => {
    stubApi({
      "GET /slos/slo-1": json(target()),
      "GET /slo-readings/slo-1/burn": json(burnLine([], null)),
    });
    const quiet = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(quiet.container).includes("No tier can judge this target yet"),
      "no line",
    );
    unmountAll();

    stubApi({
      "GET /slos/slo-1": json(target()),
      "GET /slo-readings/slo-1/burn": json({ detail: "boom" }, 500),
    });
    const failed = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(failed.container).includes("How fast could not be read"),
      "the failure line",
    );
    expect(readable(failed.container)).toContain("Failures still allowed: 360 of 672.");
  });

  it("says who is told in each of FR25a's three cases (AC84)", async () => {
    stubApi({ "GET /slos/slo-1": json(target()) });
    const told = await mountAt("/slos/slo-1");
    await waitFor(
      () =>
        readable(told.container).includes(
          "Warnings and criticals go to Organization owners, through Reliability targets.",
        ),
      "who is told",
    );
    unmountAll();

    stubApi({
      "GET /slos/slo-1": json(
        target({
          told: { alerting: false, rules: [], snoozed_until: null, snoozed_by_name: null },
        }),
      ),
    });
    const none = await mountAt("/slos/slo-1");
    await waitFor(() => readable(none.container).includes("No alerts will be sent."), "no alerts");
    unmountAll();

    stubApi({
      "GET /slos/slo-1": json(
        target({
          told: {
            ...target().told,
            snoozed_until: minutesFromNow(120),
            snoozed_by_name: "Member",
          },
        }),
      ),
    });
    const snoozed = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(snoozed.container).includes("by Member. Failures still count."),
      "snoozed",
    );
  });

  it("shows at most two notes, with the rest behind 'N more notes', the no-alerts note pinned (FR10)", async () => {
    stubApi({
      "GET /slos/slo-1": json(
        target({ qualifiers: ["no_alerts", "missed", "thin_traffic", "never_failed"] }),
      ),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("No alerts will be sent."), "the pinned note");
    expect(readable(container)).toContain("Target missed.");
    expect(readable(container)).not.toContain("Traffic is thin");
    await press(container, "2 more notes");
    expect(readable(container)).toContain("Traffic is thin");
  });

  it("shows its own failure line with a retry when it cannot load (AC82)", async () => {
    stubApi({
      "GET /slos/slo-1": json({ type: "about:blank", title: "Unavailable", status: 503 }, 503),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(
      () => readable(container).includes("This target could not be loaded"),
      "the failure",
    );
  });
});

describe("the form", () => {
  it("refuses a too-fine objective beside its field and in the summary (AC5 in Step 1's form)", async () => {
    const calls = stubApi({ "GET /projects": json({ projects: [] }) }, "member");
    const { container } = await mountAt("/slos/new");
    await waitFor(() => container.querySelector("form"), "the form");
    const objective = container.querySelector<HTMLInputElement>("input[name=objective_percent]");
    expect(objective).not.toBeNull();
    await press(container, "Save");
    expect(readable(container)).toContain("The target was not saved");
    expect(readable(container)).toContain("Name the service to count.");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("asks the sampling question and says what Yes means (FR5)", async () => {
    stubApi({ "GET /projects": json({ projects: [] }) }, "member");
    const { container } = await mountAt("/slos/new");
    await waitFor(
      () => readable(container).includes("Do you sample before sending to nexovue?"),
      "the question",
    );
    expect(readable(container)).toContain("A failure is a request whose span status is ERROR.");
  });
});

describe("the monthly answer (Step 3)", () => {
  function onTheTarget(months: Parameters<typeof monthsOf>[0], view = target()) {
    return {
      "GET /slos/slo-1": json(view),
      "GET /slos/slo-1/months": json(monthsOf(months)),
    };
  }

  it("reads a month at 89% coverage as not judged and at 91% gives its figure (AC77)", async () => {
    stubApi(
      onTheTarget([
        month({ month: "2026-10-01", coverage: 0.91 }),
        month({
          month: "2026-09-01",
          coverage: 0.89,
          result: "not_judged",
          days_in_month: 30,
          days_held: 30,
        }),
      ]),
    );
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("October 2026: 99.95%"), "the months");
    expect(readable(container)).toContain(
      "October 2026: 99.95% against 99.9%. Measured 91% of the month.",
    );
    expect(readable(container)).toContain("September 2026: not judged. Nexovue measured only 89%.");
  });

  it("shows at most 13 months, says which may still change and which changed after they ended (AC77)", async () => {
    const months = Array.from({ length: 14 }, (_, index) => {
      const at = new Date(Date.UTC(2026, 10 - index, 1));
      return month({ month: at.toISOString().slice(0, 10) });
    });
    months[0] = month({
      month: "2026-11-01",
      may_still_change: true,
      days_held: 8,
      days_in_month: 30,
    });
    months[1] = month({
      month: "2026-10-01",
      changed_at: "2026-11-03T09:00:00Z",
      changed_why: "definition",
    });
    stubApi(onTheTarget(months));
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("November 2026"), "the months");
    expect(container.querySelectorAll("[data-month]").length).toBe(13);
    expect(readable(container)).toContain(
      "May still change: some of its days are under 7 days old.",
    );
    expect(readable(container)).toContain("Holds 8 of 30 days.");
    expect(readable(container)).toContain("Changed on 3 Nov by a change to what it counts.");
  });

  it("says an exclusion removed too late to recount was removed, figure not recounted (AC69)", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    stubApi(
      onTheTarget([
        month({
          month: "2026-10-01",
          removed_not_recounted: [
            {
              id: "x-1",
              starts_at: "2026-10-03T08:00:00Z",
              minutes: 120,
              removed_at: "2026-11-08T10:00:00Z",
            },
          ],
        }),
        month({ month: "2026-09-01", days_in_month: 30, days_held: 30 }),
      ]),
    );
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("October 2026"), "the months");
    const line =
      "The exclusion from 3 Oct (2 h) was removed, figure not recounted: its days too old to count again still leave it out.";
    expect(readable(container.querySelector('[data-month="2026-10-01"]') as Element)).toContain(
      line,
    );
    expect(readable(container.querySelector('[data-month="2026-09-01"]') as Element)).not.toContain(
      "not recounted",
    );
    await press(container, "Copy");
    expect(String(writeText.mock.calls[0]?.at(0))).toContain(line);
  });

  it("tells a nightly lull from a real outage by the longest stretch and the link (AC77)", async () => {
    stubApi(
      onTheTarget([
        month({
          month: "2026-10-01",
          quiet_minutes: 400,
          longest_quiet_minutes: 190,
          time_not_counted: true,
          outages: [
            {
              kind: "traffic_finding",
              id: "f-1",
              started_at: "2026-10-14T02:00:00Z",
              minutes: 190,
            },
          ],
        }),
        month({
          month: "2026-09-01",
          quiet_minutes: 600,
          longest_quiet_minutes: 120,
          days_in_month: 30,
          days_held: 30,
        }),
      ]),
    );
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("October 2026"), "the months");
    const october = container.querySelector('[data-month="2026-10-01"]');
    const september = container.querySelector('[data-month="2026-09-01"]');
    expect(readable(october as Element)).toContain(
      "This figure does not count the outage on 14 Oct (3 h 10 m). Requests that never arrived are not failures.",
    );
    expect(october?.querySelector('a[href="/findings/f-1"]')).not.toBeNull();
    expect(readable(september as Element)).toContain("10 h with no requests, the longest 2 h.");
    expect(readable(september as Element)).not.toContain("outage");
  });

  it("says a met month and a missed rolling window are different periods (AC117)", async () => {
    const missing = target({ figures: { ...target().figures, missed_by: 40, left: 0 } });
    stubApi(onTheTarget([month()], missing));
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("October 2026"), "the months");
    expect(readable(container)).toContain("The last 28 days and October are different periods.");
  });

  it("copies a month with its period, coverage, quiet and excluded time and fixed label (AC78)", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    stubApi(onTheTarget([month({ quiet_minutes: 190, longest_quiet_minutes: 125 })]));
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => buttons(container).includes("Copy"), "the copy action");
    await press(container, "Copy");
    const copied = String(writeText.mock.calls[0]?.at(0));
    expect(copied).toContain("checkout availability, 99.9% over 28 days.");
    expect(copied).toContain("October 2026: 99.95% against 99.9%. Measured 100% of the month.");
    expect(copied).toContain("3 h 10 m with no requests, the longest 2 h 5 m.");
    expect(copied).toContain("Nothing left out for planned work.");
    expect(copied).toContain(REQUEST_LABEL);
    expect(copied).toContain(AUDIENCE);
  });

  it("offers the months as a CSV file and says who a figure is for", async () => {
    stubApi(onTheTarget([month()]));
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => readable(container).includes("October 2026"), "the months");
    const download = container.querySelector('a[href*="slos/months.csv"]');
    expect(download?.getAttribute("href")).toContain("slo_id=slo-1");
    expect(readable(container)).toContain(AUDIENCE);
  });

  it("lists each change on the History tab (FR30)", async () => {
    stubApi({
      "GET /slos/slo-1": json(target()),
      "GET /slos/slo-1/history": json({
        entries: [
          {
            at: "2026-11-03T09:00:00Z",
            what: "changed",
            before: { name: "checkout availability", objective_percent: "99.90" },
            after: { name: "checkout availability", objective_percent: "99.5" },
            by_name: "Member",
          },
          { at: "2026-10-01T09:00:00Z", what: "created", before: null, after: {}, by_name: null },
        ],
      }),
    });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => buttons(container).includes("History"), "the tabs");
    await press(container, "History");
    await waitFor(() => readable(container).includes("Changed by Member"), "the history");
    expect(readable(container)).toContain("Target: 99.90 → 99.5");
    expect(readable(container)).not.toContain("Name: checkout");
  });

  it("explains the error budget on the Definition tab, and only there (AC13)", async () => {
    stubApi({ "GET /slos/slo-1": json(target()) });
    const { container } = await mountAt("/slos/slo-1");
    await waitFor(() => buttons(container).includes("Definition"), "the tabs");
    expect(readable(container)).not.toContain("error budget");
    await press(container, "Definition");
    expect(readable(container)).toContain("error budget");
    expect(readable(container)).toContain("A failure is a request whose span status is ERROR.");
  });
});
