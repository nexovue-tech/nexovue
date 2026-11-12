// @vitest-environment jsdom

import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardSummary } from "./api";
import { DashboardListScreen } from "./DashboardListScreen";
import {
  buttonNamed,
  DAY_MS,
  json,
  mountScreen,
  NOW,
  problem,
  settle,
  text,
  typeInto,
  unmountAll,
} from "./render-harness.test-util";

/**
 * The dashboard list (F20, T7.2; FR36, FR58, AC45, AC46, AC47, AC81, AC96): columns, sort
 * buttons with `aria-sort`, the 30-day mark, "3 panels not reading", a former member, the
 * title filter and each state. The clock is pinned and passed in.
 */

const ago = (days: number) => new Date(NOW - days * DAY_MS).toISOString();

function row(title: string, changes: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    id: `id-${title}`,
    title,
    description: "",
    owner: "Asha",
    panel_count: 12,
    uses_picker: false,
    panels_not_reading: 0,
    last_viewed_at: ago(0.1),
    created_at: ago(90),
    updated_at: ago(3),
    deleted_at: null,
    ...changes,
  };
}

const TEMPLATES = {
  templates: [{ id: "start_from_overview", name: "Start from Overview", document: {} }],
};

function lists(live: DashboardSummary[], deleted: DashboardSummary[] = []) {
  return (request: { method: string; path: string; search: string }) => {
    if (request.path === "/dashboards/templates") return json(TEMPLATES);
    if (request.path === "/dashboards") {
      return json({
        dashboards: request.search.includes("deleted=true") ? deleted : live,
        more: false,
      });
    }
    return undefined;
  };
}

const titlesShown = (container: Element) =>
  [...container.querySelectorAll("tbody tr")].map(
    (each) => each.querySelector("a")?.textContent ?? "",
  );
const header = (container: Element, name: string) =>
  [...container.querySelectorAll("th")].find((each) => text(each).startsWith(name));

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  unmountAll();
  vi.useRealTimers();
});

const screen = () => createElement(DashboardListScreen, { now: NOW });

describe("rows", () => {
  it("shows title, description, owner, panels, last opened, last changed and not reading", async () => {
    const { container } = await mountScreen(
      screen(),
      lists([
        row("Checkout on-call", { description: "What checkout watches", panels_not_reading: 3 }),
      ]),
    );

    const cells = text(container.querySelector("tbody"));
    expect(cells).toContain("Checkout on-call");
    expect(cells).toContain("What checkout watches");
    expect(cells).toContain("Asha");
    expect(cells).toContain("12");
    expect(cells).toContain("3 panels not reading");
    expect(
      [...container.querySelectorAll("th")].map((each) => text(each).replace(/[▲▼]/g, "").trim()),
    ).toEqual(["Title", "Owner", "Panels", "Last opened", "Last changed", "Not reading"]);
  });

  it("reads Former member for a dashboard whose owner left, and still links to it (AC47)", async () => {
    const { container } = await mountScreen(screen(), lists([row("Orphan", { owner: null })]));

    expect(text(container.querySelector("tbody"))).toContain("Former member");
    expect(container.querySelector("tbody a")?.getAttribute("href")).toBe("/dashboards/id-Orphan");
  });

  it("marks a dashboard not opened for 30 days, and not one opened 29 days ago (AC45)", async () => {
    const { container } = await mountScreen(
      screen(),
      lists([
        row("Old staging", { last_viewed_at: ago(41) }),
        row("Recent", { last_viewed_at: ago(29) }),
        row("Never", { last_viewed_at: null, created_at: ago(2) }),
      ]),
    );

    const rows = [...container.querySelectorAll("tbody tr")].map((each) => text(each));
    expect(rows.find((each) => each.startsWith("Old staging"))).toContain("not opened for 30 days");
    expect(rows.find((each) => each.startsWith("Recent"))).not.toContain("not opened");
    expect(rows.find((each) => each.startsWith("Never"))).toContain("Never");
    expect(rows.find((each) => each.startsWith("Never"))).not.toContain("not opened");
  });
});

describe("sorting (AC46)", () => {
  const data = [
    row("Opened lately", { last_viewed_at: ago(1) }),
    row("Never opened", { last_viewed_at: null }),
    row("Opened long ago", { last_viewed_at: ago(40) }),
  ];

  it("starts by title, with aria-sort on every sortable header and ascending on the title", async () => {
    const { container } = await mountScreen(screen(), lists(data));

    expect(titlesShown(container)).toEqual(["Never opened", "Opened lately", "Opened long ago"]);
    const marks = [...container.querySelectorAll("th")].map((each) =>
      each.getAttribute("aria-sort"),
    );
    expect(marks).toEqual(["ascending", "none", "none", "none", "none", "none"]);
  });

  it("puts never-opened first when last opened is pressed once, and last when pressed again", async () => {
    const { container } = await mountScreen(screen(), lists(data));

    act(() => header(container, "Last opened")?.querySelector("button")?.click());
    expect(titlesShown(container)).toEqual(["Never opened", "Opened long ago", "Opened lately"]);
    expect(header(container, "Last opened")?.getAttribute("aria-sort")).toBe("ascending");

    act(() => header(container, "Last opened")?.querySelector("button")?.click());
    expect(titlesShown(container)).toEqual(["Opened lately", "Opened long ago", "Never opened"]);
    expect(header(container, "Last opened")?.getAttribute("aria-sort")).toBe("descending");
    expect(header(container, "Title")?.getAttribute("aria-sort")).toBe("none");
  });
});

describe("the filter and the states (AC81, FR58)", () => {
  it("filters by title, and a filter that excludes everything names itself and can be cleared", async () => {
    const { container } = await mountScreen(
      screen(),
      lists([row("Checkout on-call"), row("Billing")]),
    );
    const input = container.querySelector<HTMLInputElement>('input[type="search"]');

    act(() => typeInto(input as HTMLInputElement, "bill"));
    expect(titlesShown(container)).toEqual(["Billing"]);

    act(() => typeInto(input as HTMLInputElement, "zzz"));
    expect(container.querySelector("table")).toBeNull();
    expect(text(container)).toContain("No dashboard has “zzz” in its title.");
    // Never the never-had-any state.
    expect(text(container)).not.toContain("No dashboards yet");

    act(() => buttonNamed(container, "Clear filter")?.click());
    expect(titlesShown(container)).toHaveLength(2);
  });

  it("is a skeleton while loading", async () => {
    const { container } = await mountScreen(screen(), () => new Promise(() => {}) as never);
    expect(
      container.querySelector('[aria-label="Loading dashboards"], [role="status"]'),
    ).not.toBeNull();
  });

  it("says it could not list them, with a retry that asks again", async () => {
    let failing = true;
    const { container, calls } = await mountScreen(screen(), (request) => {
      if (request.path === "/dashboards" && !request.search.includes("deleted")) {
        return failing
          ? problem(500, "The database is away.")
          : json({ dashboards: [row("Back")], more: false });
      }
      return lists([])(request);
    });
    expect(text(container)).toContain("Could not list dashboards");

    failing = false;
    await act(async () => buttonNamed(container, "Try again")?.click());
    await settle();

    expect(titlesShown(container)).toEqual(["Back"]);
    expect(
      calls.filter((call) => call.path === "/dashboards" && !call.search.includes("deleted"))
        .length,
    ).toBeGreaterThan(1);
  });

  it("offers an editor each starting point, then a blank dashboard, when there is none", async () => {
    const { container } = await mountScreen(screen(), lists([]));

    expect(text(container)).toContain("No dashboards yet");
    expect([...container.querySelectorAll("a")].map((each) => text(each))).toEqual([
      "Start from Overview",
      "New dashboard",
    ]);
  });

  it("tells a viewer to ask a member, and offers nothing to create", async () => {
    const { container } = await mountScreen(screen(), lists([]), "viewer");

    expect(text(container)).toContain("Ask a member to create one.");
    expect(container.querySelectorAll("a")).toHaveLength(0);
  });
});

describe("Recently deleted", () => {
  it("counts what can be restored, and opens the list on request", async () => {
    const gone = row("Retired", { deleted_at: ago(2) });
    const { container } = await mountScreen(screen(), lists([row("Live")], [gone]));

    expect(buttonNamed(container, "Recently deleted (1)")).toBeDefined();
    expect(text(container)).not.toContain("Retired");

    act(() => buttonNamed(container, "Recently deleted (1)")?.click());
    await settle();

    expect(text(container)).toContain("Retired");
    expect(buttonNamed(container, "Recently deleted (1)")?.getAttribute("aria-expanded")).toBe(
      "true",
    );
  });
});
