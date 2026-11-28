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
 * title filter and each state. Step 9 (T9.1, T9.3): the starting points in FR39's order, a
 * saved one as "Open", and import's states (FR42, AC128, AC139). The clock is pinned and
 * passed in.
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

/** The API's three starting points, in its order (FR39), each with its document's title. */
const template = (id: string, name: string, title: string) => ({
  id,
  name,
  document: { title, description: "", definition: {} },
});
const TEMPLATES = {
  templates: [
    template("start_from_overview", "Start from Overview", "Overview"),
    template("service_health", "Service health", "Service health"),
    template("infrastructure", "Infrastructure", "Infrastructure"),
  ],
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

  it("never leaves the Not reading cell blank: None for a zero, a dash for a row never opened", async () => {
    const { container } = await mountScreen(
      screen(),
      lists([
        row("Reads fine", { panels_not_reading: 0 }),
        row("Never opened", { last_viewed_at: null }),
      ]),
    );

    const lastCells = [...container.querySelectorAll("tbody tr")].map((each) =>
      text(each.querySelector("td:last-child")),
    );
    expect(lastCells).toEqual(["—", "None"]); // Title order: Never opened, Reads fine.
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

  it("offers an editor each starting point in FR39's order, then a blank dashboard (FR39)", async () => {
    const { container } = await mountScreen(screen(), lists([]));

    expect(text(container)).toContain("No dashboards yet");
    expect(text(container)).toContain("Nothing is saved until you save it.");
    const links = [...container.querySelectorAll("a")];
    expect(links.map((each) => text(each))).toEqual([
      "Start from Overview",
      "Service health",
      "Infrastructure",
      "New dashboard",
    ]);
    // Each opens as a draft: a link, never a save (AC38).
    expect(links.map((each) => each.getAttribute("href"))).toEqual([
      "/dashboards/new?template=start_from_overview",
      "/dashboards/new?template=service_health",
      "/dashboards/new?template=infrastructure",
      "/dashboards/new",
    ]);
    expect(buttonNamed(container, "Import")).toBeDefined();
  });

  it("tells a viewer to ask a member, and offers nothing to create or import", async () => {
    const { container } = await mountScreen(screen(), lists([]), "viewer");

    expect(text(container)).toContain("Ask a member to create one.");
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(buttonNamed(container, "Import")).toBeUndefined();
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });
});

describe("starting points already saved (FR39, D20)", () => {
  it("offers a saved template as Open, straight to it, and never a second copy", async () => {
    const saved = row("service HEALTH", { id: "sh-1" });
    const { container } = await mountScreen(screen(), lists([row("Checkout on-call"), saved]));

    const links = [...container.querySelectorAll("header a")];
    expect(links.map((each) => text(each))).toEqual([
      "Start from Overview",
      "Open service HEALTH",
      "Infrastructure",
      "New dashboard",
    ]);
    expect(links[1].getAttribute("href")).toBe("/dashboards/sh-1");
  });
});

/** A chosen file, as the browser hands it to the input. */
async function choose(container: Element, file: File): Promise<void> {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("no file input");
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
}

const DOCUMENT = {
  title: "Imported board",
  description: "",
  definition: {
    schema_version: 1,
    refresh_seconds: 60,
    default_range: null,
    service_picker: false,
    links: [],
    panels: [],
  },
};

const fileOf = (body: string, name = "board.json") =>
  new File([body], name, { type: "application/json" });

describe("import (FR42, AC128, AC139)", () => {
  it("sends a file to the check, and opens what passed in the builder", async () => {
    const answer = lists([]);
    const { container, calls, router } = await mountScreen(screen(), (request) =>
      request.path === "/dashboards/validate"
        ? json({ document: DOCUMENT, panel_count: 0, worst_case_reads: 0 })
        : answer(request),
    );

    await choose(container, fileOf(JSON.stringify(DOCUMENT)));

    const sent = calls.filter((call) => call.path === "/dashboards/validate");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ method: "POST", body: DOCUMENT });
    expect(router.state.location.pathname).toBe("/dashboards/new");
    expect(router.state.location.search).toEqual({ file: expect.any(String) });
    // Nothing is written by importing: the draft is saved only from the builder (FR42).
    expect(calls.filter((call) => call.method === "POST" && call.path === "/dashboards")).toEqual(
      [],
    );
  });

  it("refuses a 65 KiB file in words, sending nothing (AC139)", async () => {
    const { container, calls, router } = await mountScreen(screen(), lists([]));

    await choose(container, fileOf(`{"title":"${"x".repeat(65 * 1024)}"}`));

    expect(text(container)).toContain("This file was not opened:");
    expect(text(container)).toContain(
      "This file is 66 KiB, and a dashboard file is at most 64 KiB.",
    );
    expect(calls.filter((call) => call.path === "/dashboards/validate")).toEqual([]);
    expect(router.state.location.pathname).toBe("/");
  });

  it("refuses a file that is not JSON, sending nothing", async () => {
    const { container, calls } = await mountScreen(screen(), lists([]));

    await choose(container, fileOf("SELECT * FROM organizations"));

    expect(text(container)).toContain("This file is not a dashboard file: it is not JSON.");
    expect(calls.filter((call) => call.path === "/dashboards/validate")).toEqual([]);
  });

  it("says what the check refused, field by field, and opens nothing (AC128, AC41)", async () => {
    const answer = lists([]);
    const { container, router } = await mountScreen(screen(), (request) =>
      request.path === "/dashboards/validate"
        ? problem(422, "Request validation failed", {
            errors: [
              {
                field: "body.definition.links.0.url",
                reason: "Value error, must be an https address",
              },
              {
                field: "body.definition.schema_version",
                reason:
                  "Value error, this dashboard is in format 2, made by a newer version of nexovue",
              },
            ],
          })
        : answer(request),
    );

    await choose(container, fileOf(JSON.stringify(DOCUMENT)));

    expect(text(container)).toContain("This file was not opened:");
    expect(text(container)).toContain("Link 1 address: must be an https address");
    expect(text(container)).toContain(
      "Format: this dashboard is in format 2, made by a newer version of nexovue",
    );
    expect(router.state.location.pathname).toBe("/");
  });

  it("says the check could not run, and tries the same file again on request", async () => {
    const answer = lists([]);
    let down = true;
    const { container, calls, router } = await mountScreen(screen(), (request) => {
      if (request.path !== "/dashboards/validate") return answer(request);
      return down
        ? problem(503, "unavailable")
        : json({ document: DOCUMENT, panel_count: 0, worst_case_reads: 0 });
    });

    await choose(container, fileOf(JSON.stringify(DOCUMENT)));
    expect(text(container)).toContain("Could not check the file.");
    expect(router.state.location.pathname).toBe("/");

    down = false;
    await act(async () => buttonNamed(container, "Try again")?.click());
    await settle();

    const sent = calls.filter((call) => call.path === "/dashboards/validate");
    expect(sent.map((call) => call.body)).toEqual([DOCUMENT, DOCUMENT]);
    expect(router.state.location.pathname).toBe("/dashboards/new");
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
