// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-12T12:00:00Z"));
});
afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

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

  it("says CI and traffic name versions differently in place of never seen (AC78)", async () => {
    stubApi({
      "GET /releases": json(
        listOf(
          [
            row({ version: "1.5.0", activity: "not_seen", basis: "reported", last_seen_at: null }),
            row({
              id: "r2",
              service_name: "payments",
              version: "2.0.0",
              activity: "not_seen",
              basis: "reported",
              last_seen_at: null,
              started_at: minutesAgo(90),
            }),
          ],
          {
            naming_differs: [
              { service_name: "checkout", reported_version: "1.5.0", seen_version: "abc123" },
            ],
          },
        ),
      ),
    });
    const { container } = await mountAt("/releases");
    await waitFor(
      () =>
        readable(container).includes(
          "Your CI and your traffic name versions differently: CI sent 1.5.0, traffic sends abc123",
        ),
      "FR46's words",
    );
    expect(readable(container)).toContain("Reported, never seen in traffic.");
  });

  it("names the services we hear nothing about, with setup for those who can act", async () => {
    stubApi({
      "GET /releases": json(listOf([row()])),
      "GET /releases/coverage": json({
        at: minutesAgo(0),
        services: [
          {
            service_name: "search",
            coverage: "none",
            last_reported_at: null,
            first_heard_at: null,
          },
          {
            service_name: "checkout",
            coverage: "versions_only",
            last_reported_at: null,
            first_heard_at: null,
          },
        ],
      }),
    });
    const { container } = await mountAt("/releases");
    await waitFor(
      () => readable(container).includes("We receive no deploy information for search."),
      "the coverage strip",
    );
    expect(readable(container)).toContain("Set up deploy reporting");
    expect(readable(container)).not.toContain("information for search, checkout");
  });

  it("renders a hostile version as text (AC43)", async () => {
    stubApi({ "GET /releases": json(listOf([row({ version: "<script>alert(1)</script>" })])) });
    const { container } = await mountAt("/releases");
    await waitFor(() => readable(container).includes("<script>"), "the version as text");
    expect(container.querySelector("script")).toBeNull();
  });
});
