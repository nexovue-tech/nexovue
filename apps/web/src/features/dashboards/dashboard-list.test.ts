import { describe, expect, it } from "vitest";

import type { DashboardSummary } from "./api";
import {
  DEFAULT_ORDER,
  filterByTitle,
  isStale,
  nextOrder,
  notReadingWords,
  sortRows,
  startingPoints,
} from "./dashboard-list";

/** The list's order, filter and marks as data (F20, FR36, AC45, AC46, AC81). */

const NOW = Date.parse("2026-11-12T10:00:00Z");
const DAY = 86_400_000;
const ago = (days: number) => new Date(NOW - days * DAY).toISOString();

function row(id: string, changes: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    id,
    title: id,
    description: "",
    owner: "Asha",
    panel_count: 3,
    uses_picker: false,
    panels_not_reading: 0,
    last_viewed_at: ago(1),
    created_at: ago(90),
    updated_at: ago(2),
    deleted_at: null,
    ...changes,
  };
}

const titles = (rows: DashboardSummary[]) => rows.map((each) => each.title);

describe("order (AC46)", () => {
  const rows = [
    row("Opened lately", { last_viewed_at: ago(1) }),
    row("Never opened", { last_viewed_at: null }),
    row("Opened long ago", { last_viewed_at: ago(40) }),
  ];

  it("puts never-opened dashboards first in the least opened order", () => {
    expect(titles(sortRows(rows, { column: "opened", direction: "ascending" }))).toEqual([
      "Never opened",
      "Opened long ago",
      "Opened lately",
    ]);
  });

  it("puts them last when the most recently opened come first", () => {
    expect(titles(sortRows(rows, { column: "opened", direction: "descending" }))).toEqual([
      "Opened lately",
      "Opened long ago",
      "Never opened",
    ]);
  });

  it("orders by title ignoring case, by owner with a former member as such, and by count", () => {
    const mixed = [
      row("b", { owner: null, panel_count: 9, panels_not_reading: 2, updated_at: ago(1) }),
      row("A", { owner: "Zed", panel_count: 1, updated_at: ago(5) }),
      row("c", { owner: "Asha", panel_count: 5, panels_not_reading: 1, updated_at: ago(3) }),
    ];
    const by = (column: Parameters<typeof sortRows>[1]["column"]) =>
      titles(sortRows(mixed, { column, direction: "ascending" }));

    expect(by("title")).toEqual(["A", "b", "c"]);
    expect(by("owner")).toEqual(["c", "b", "A"]);
    expect(by("panels")).toEqual(["A", "c", "b"]);
    expect(by("changed")).toEqual(["A", "c", "b"]);
    expect(by("not_reading")).toEqual(["A", "c", "b"]);
  });

  it("breaks ties by title, so the order is the same on every draw", () => {
    const tied = [row("b"), row("a")];
    expect(titles(sortRows(tied, { column: "panels", direction: "descending" }))).toEqual([
      "a",
      "b",
    ]);
  });

  it("asking again reverses the column; a new column starts ascending", () => {
    expect(nextOrder(DEFAULT_ORDER, "title").direction).toBe("descending");
    expect(nextOrder({ column: "title", direction: "descending" }, "opened")).toEqual({
      column: "opened",
      direction: "ascending",
    });
  });
});

describe("filter (AC81)", () => {
  const rows = [row("Checkout on-call"), row("Billing"), row("checkout latency")];

  it("matches a title ignoring case and the spaces around the text", () => {
    expect(titles(filterByTitle(rows, "  CHECKOUT "))).toEqual([
      "Checkout on-call",
      "checkout latency",
    ]);
  });

  it("is everything when nothing is typed, and nothing when nothing matches", () => {
    expect(filterByTitle(rows, "   ")).toHaveLength(3);
    expect(filterByTitle(rows, "zzz")).toEqual([]);
  });
});

describe("marks (AC45)", () => {
  it("marks a dashboard not opened for more than 30 days, and not one opened 29 days ago", () => {
    expect(isStale(row("x", { last_viewed_at: ago(31) }), NOW)).toBe(true);
    expect(isStale(row("x", { last_viewed_at: ago(29) }), NOW)).toBe(false);
  });

  it("counts a never-opened dashboard from when it was made", () => {
    expect(isStale(row("x", { last_viewed_at: null, created_at: ago(40) }), NOW)).toBe(true);
    expect(isStale(row("x", { last_viewed_at: null, created_at: ago(3) }), NOW)).toBe(false);
  });

  it("says how many panels are not reading, and nothing when none are", () => {
    expect(notReadingWords(3)).toBe("3 panels not reading");
    expect(notReadingWords(1)).toBe("1 panel not reading");
    expect(notReadingWords(0)).toBeUndefined();
  });
});

describe("the starting points (FR39)", () => {
  const template = (id: string, name: string, title: string) => ({
    id,
    name,
    document: {
      title,
      description: "",
      definition: {
        schema_version: 1 as const,
        refresh_seconds: null,
        default_range: null,
        service_picker: false,
        links: [],
        panels: [],
      },
    },
  });
  const templates = [
    template("start_from_overview", "Start from Overview", "Overview"),
    template("service_health", "Service health", "Service health"),
    template("infrastructure", "Infrastructure", "Infrastructure"),
  ];

  it("keeps the API's order, and offers each to start while none is saved", () => {
    expect(startingPoints(templates, [row("a", { title: "Checkout" })])).toEqual([
      { kind: "start", templateId: "start_from_overview", label: "Start from Overview" },
      { kind: "start", templateId: "service_health", label: "Service health" },
      { kind: "start", templateId: "infrastructure", label: "Infrastructure" },
    ]);
  });

  it("offers a saved one as Open, by the dashboard's own title and id", () => {
    const points = startingPoints(templates, [row("ov", { title: "overview" })]);

    expect(points[0]).toEqual({
      kind: "open",
      templateId: "start_from_overview",
      dashboardId: "ov",
      label: "Open overview",
    });
    expect(points.slice(1).map((point) => point.kind)).toEqual(["start", "start"]);
  });
});
