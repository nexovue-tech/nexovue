import { describe, expect, it } from "vitest";

import {
  SERVICE_HEALTH,
  savedUnder,
  serviceHealthOffer,
  windowForClosed,
  windowForOpen,
} from "./dashboards-for-service";

/** The window a dashboard opens on from a finding or an incident (F20, FR57, AC102, AC117). */

const NOW = Date.parse("2026-11-12T10:00:00Z");
const MIN = 60_000;
const ago = (minutes: number) => new Date(NOW - minutes * MIN).toISOString();

describe("from something still open", () => {
  it("is the shortest live range that starts before it began, with a lead", () => {
    // 4 minutes + 10 lead fits the 15 minutes; 6 + 10 does not.
    expect(windowForOpen(ago(4), NOW)).toEqual({ kind: "live", rangeId: "15m" });
    expect(windowForOpen(ago(6), NOW)).toEqual({ kind: "live", rangeId: "1h" });
    expect(windowForOpen(ago(55), NOW)).toEqual({ kind: "live", rangeId: "6h" });
    expect(windowForOpen(ago(60 * 20), NOW)).toEqual({ kind: "live", rangeId: "24h" });
  });

  it("is the longest range there is when it began longer ago than that", () => {
    expect(windowForOpen(ago(60 * 24 * 90), NOW)).toEqual({ kind: "live", rangeId: "30d" });
  });
});

describe("from something over", () => {
  it("is fixed times with a margin of a quarter of its length on each side", () => {
    expect(windowForClosed(ago(120), ago(60), NOW)).toEqual({
      kind: "fixed",
      from: "2026-11-12T07:45:00.000Z",
      to: "2026-11-12T09:15:00.000Z",
    });
  });

  it("keeps at least 10 minutes of margin for a short one", () => {
    expect(windowForClosed(ago(60), ago(58), NOW)).toEqual({
      kind: "fixed",
      from: "2026-11-12T08:50:00.000Z",
      to: "2026-11-12T09:12:00.000Z",
    });
  });

  it("ends no later than this minute, so a link never asks for the future", () => {
    expect(windowForClosed(ago(30), ago(1), NOW)).toMatchObject({ to: "2026-11-12T10:00:00.000Z" });
  });

  it("is never longer than 30 days, keeping the end", () => {
    const window = windowForClosed(ago(60 * 24 * 60), ago(60), NOW);
    expect(window.kind).toBe("fixed");
    if (window.kind !== "fixed") return;
    expect(Date.parse(window.to) - Date.parse(window.from)).toBe(30 * 24 * 60 * MIN);
    expect(Date.parse(window.to)).toBeGreaterThan(Date.parse(window.from));
  });
});

describe("a saved template, by its title (FR39, FR41, D20)", () => {
  const templates = [
    { id: "start_from_overview", name: "Start from Overview", document: { title: "Overview" } },
    { id: SERVICE_HEALTH, name: "Service health", document: { title: "Service health" } },
  ];

  it("is the dashboard holding the title, ignoring case as the API's index does", () => {
    const rows = [
      { id: "a", title: "Service health (copy)" },
      { id: "b", title: "SERVICE HEALTH" },
    ];
    expect(savedUnder("Service health", rows)).toEqual({ id: "b", title: "SERVICE HEALTH" });
    expect(savedUnder("Service health", [{ id: "a", title: "Service healthy" }])).toBeUndefined();
  });

  it("offers Service health to start until it is saved, then only to open (AC138)", () => {
    expect(serviceHealthOffer(templates, [{ id: "x", title: "Overview" }])).toEqual({
      kind: "start",
      templateId: SERVICE_HEALTH,
    });
    expect(serviceHealthOffer(templates, [{ id: "sh", title: "service health" }])).toEqual({
      kind: "open",
      dashboardId: "sh",
      title: "service health",
    });
  });

  it("offers nothing when the API names no Service health", () => {
    expect(serviceHealthOffer(templates.slice(0, 1), [])).toEqual({ kind: "none" });
  });
});
