// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  json,
  listOf,
  minutesAgo,
  mountAt,
  press,
  readable,
  row,
  stubApi,
  unmountAll,
  waitFor,
} from "./release-harness.test-util";

afterEach(() => unmountAll());

describe("the releases list (AC27)", () => {
  it("states the window's count and lists each release with its source and activity", async () => {
    stubApi({
      "GET /releases": json(
        listOf([row(), row({ id: "r2", version: "v2.0", activity: "replaced" })], {
          total: 2,
          services: 1,
        }),
      ),
    });
    const { container } = await mountAt("/releases");
    await waitFor(
      () => readable(container).includes("2 releases in this window on 1 service."),
      "the count",
    );
    expect(readable(container)).toContain("Seen in traffic");
    expect(readable(container)).toContain("Serving");
    expect(readable(container)).toContain("Replaced");
  });

  it("shows a version serving since yesterday in today's window", async () => {
    stubApi({
      "GET /releases": json(listOf([row({ version: "v1.9", started_at: minutesAgo(60 * 26) })])),
    });
    const { container } = await mountAt("/releases");
    await waitFor(() => readable(container).includes("v1.9"), "yesterday's release");
  });

  it("reads differently when the project has no release at all", async () => {
    stubApi({ "GET /releases": json(listOf([], { has_any: false })) });
    const { container } = await mountAt("/releases");
    await waitFor(() => readable(container).includes("No releases yet."), "the empty state");
    expect(readable(container)).toContain("service.version");
  });

  it("names the latest release when nothing falls in the window", async () => {
    stubApi({
      "GET /releases": json(
        listOf([], {
          has_any: true,
          latest: {
            id: "old",
            service_name: "checkout",
            version: "v2.1",
            started_at: minutesAgo(60 * 24 * 3),
          },
        }),
      ),
    });
    const { container } = await mountAt("/releases");
    await waitFor(
      () => readable(container).includes("The latest was v2.1 of checkout, 3 days ago."),
      "the latest",
    );
    expect(readable(container)).toMatch(/^.*No releases in the last /);
  });

  it("says a filter matched nothing, and offers to clear it", async () => {
    const calls = stubApi({
      "GET /releases": (url) =>
        json(
          url.searchParams.get("service") === "checkout"
            ? listOf([], { has_any: true })
            : listOf([row()]),
        )(url),
    });
    const { container } = await mountAt("/releases");
    await waitFor(() => container.querySelector("input[name=service]"), "the filter");
    const field = container.querySelector<HTMLInputElement>("input[name=service]");
    await act(async () => {
      if (field === null) return;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(field, "checkout");
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await press(container, "Filter");
    await waitFor(
      () => readable(container).includes("No releases of checkout in this window."),
      "the zero results",
    );
    expect(calls.some((url) => url.searchParams.get("service") === "checkout")).toBe(true);
    await press(container, "Clear the filter");
    await waitFor(() => readable(container).includes("v2.1"), "the list again");
  });

  it("pages with the cursor the API gave", async () => {
    const calls = stubApi({
      "GET /releases": (url) =>
        json(
          url.searchParams.get("cursor") === "next"
            ? listOf([row({ id: "r3", version: "v1.0" })], { total: 2 })
            : listOf([row()], { total: 2, next_cursor: "next" }),
        )(url),
    });
    const { container } = await mountAt("/releases");
    await waitFor(() => readable(container).includes("v2.1"), "the first page");
    await press(container, "Next page");
    await waitFor(() => readable(container).includes("v1.0"), "the second page");
    expect(calls.some((url) => url.searchParams.get("cursor") === "next")).toBe(true);
  });

  it("renders a hostile version as text (AC43)", async () => {
    stubApi({ "GET /releases": json(listOf([row({ version: "<script>alert(1)</script>" })])) });
    const { container } = await mountAt("/releases");
    await waitFor(() => readable(container).includes("<script>"), "the version as text");
    expect(container.querySelector("script")).toBeNull();
  });
});

describe("a release's page", () => {
  const release = {
    id: "release-1",
    project_id: "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f",
    service_name: "checkout",
    version: "v2.1",
    basis: "seen",
    activity: "serving",
    replaced_by: null,
    first_seen_at: minutesAgo(50),
    last_seen_at: minutesAgo(1),
    first_reported_at: null,
    last_reported_at: null,
    report_count: 0,
    last_state: null,
    periods: [
      {
        id: "period-1",
        started_at: minutesAgo(50),
        ended_at: null,
        start_basis: "seen",
        previous_version: "v2.0",
        already_running: false,
        is_return: false,
      },
    ],
  };

  it("leads with the comparison's sentence and says it does not say why", async () => {
    stubApi({
      "GET /releases/release-1": json(release),
      "GET /releases/release-1/comparison": json({
        period_id: "period-1",
        version: "v2.1",
        previous_version: "v2.0",
        reason: null,
        lead: "rollout",
        as_of: null,
        compared_to: minutesAgo(8),
        window_hours: 1,
        blocks: [
          {
            kind: "rollout",
            method: "side_by_side",
            from: minutesAgo(50),
            to: minutesAgo(8),
            minutes_compared: 42,
            this_version: { version: "v2.1", requests: 6000, errors: 180, p95_ms: 412 },
            previous_version: { version: "v2.0", requests: 6000, errors: 12, p95_ms: 398 },
            failure_share: { reading: "higher", note: null },
            p95: { reading: "about_same", note: null },
          },
        ],
        traffic: null,
        other_versions: [],
        numbers_kept_days: 30,
      }),
    });
    const { container } = await mountAt("/releases/release-1");
    await waitFor(
      () =>
        readable(container).includes(
          "Side by side over 42 minutes: failure share 3.0% on v2.1 against 0.2% on v2.0, higher.",
        ),
      "the sentence",
    );
    expect(readable(container)).toContain("This compares numbers. It does not say why they moved.");
    expect(readable(container)).toContain("Counts assume you do not sample before sending.");
    expect(container.querySelector("[role=status]")).not.toBeNull();
  });

  it("keeps the facts when the comparison fails (AC68's first half)", async () => {
    stubApi({
      "GET /releases/release-1": json(release),
      "GET /releases/release-1/comparison": json({ title: "Unavailable", status: 503 }, 503),
    });
    const { container } = await mountAt("/releases/release-1");
    await waitFor(
      () => readable(container).includes("The comparison could not be read"),
      "the section's failure",
    );
    expect(readable(container)).toContain("Previous version");
    expect(readable(container)).toContain("v2.0");
  });

  it("opened from a finding 5 hours on, compares over 6 hours and links back (FR22, AC64)", async () => {
    const periodStart = release.periods[0]?.started_at ?? "";
    const findingAt = new Date(Date.parse(periodStart) + 5 * 3_600_000).toISOString();
    const calls = stubApi({
      "GET /releases/release-1": json(release),
      "GET /releases/release-1/comparison": json({ title: "Unavailable", status: 503 }, 503),
    });
    const { container } = await mountAt(
      `/releases/release-1?period=period-1&finding=f-9&finding_at=${encodeURIComponent(findingAt)}`,
    );
    await waitFor(
      () =>
        readable(container).includes("Compared over 6 hours, to include the finding that started"),
      "the window's sentence",
    );
    const asked = calls.find((url) => url.pathname.endsWith("/comparison"));
    expect(asked?.searchParams.get("window")).toBe("6");
    const back = [...container.querySelectorAll("a")].find(
      (link) => link.textContent === "Back to the finding",
    );
    expect(back?.getAttribute("href")).toBe("/findings/f-9");
  });
});
