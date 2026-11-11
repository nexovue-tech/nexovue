// @vitest-environment jsdom

import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import type { ContextFinding, DashboardContext } from "./api";
import { WindowStrip } from "./WindowStrip";
import { FINDINGS_DIFFER, stripEntries, truncationWords } from "./window-strip";

/**
 * "In this window" (F20, FR47, FR67, FR70, T5.5): open findings first, critical first,
 * as the feed's own sentences linking to the finding (AC65); closed findings marked when
 * they ended; alert incidents; then releases, a version only seen never called a deploy
 * (AC122); past five, a count; FR70's line whenever a finding is listed (AC113); each cap
 * said. Fixed times on UTC: no wall clock is read.
 */

const START = "2026-11-09T02:00:00.000Z";
const END = "2026-11-09T03:00:00.000Z";

const finding = (
  id: string,
  severity: "critical" | "warning",
  started: string,
  closed: string | null = null,
): ContextFinding => ({
  id,
  scope: "service",
  service_name: "checkout",
  severity,
  title: `checkout is slower since ${started.slice(11, 16)}`,
  started_at: started,
  closed_at: closed,
});

function context(changes: Partial<DashboardContext> = {}): DashboardContext {
  return {
    project: { id: "p", name: "production", timezone: "UTC", retention: null },
    window_start: START,
    window_end: END,
    detection: {
      state: "judging",
      services_total: 1,
      services_learning: 0,
      services_judged: 1,
      services_delayed: 0,
      services_muted: 0,
      earliest_judging_date: null,
      not_running_since: null,
      delayed_since: null,
      open_findings: 1,
      services_too_many: 0,
      judged_service_limit: 50,
    },
    ever_reported: true,
    last_seen_at: END,
    findings: [
      finding("f-closed", "critical", "2026-11-09T02:05:00.000Z", "2026-11-09T02:40:00.000Z"),
      finding("f-warning", "warning", "2026-11-09T02:30:00.000Z"),
      finding("f-critical", "critical", "2026-11-09T02:12:00.000Z"),
    ],
    findings_truncated: false,
    open_findings: { total: 2, by_service: { checkout: 2 } },
    markers: [
      {
        release_id: "r1",
        period_id: null,
        deploy_id: "d1",
        service_name: "checkout",
        version: "v2.1",
        at: "2026-11-09T02:11:00.000Z",
        basis: "reported",
        state: "finished",
      },
      {
        release_id: "r2",
        period_id: "pe2",
        deploy_id: null,
        service_name: "checkout",
        version: "v2.2‮",
        at: "2026-11-09T02:20:00.000Z",
        basis: "seen",
        state: null,
      },
    ],
    markers_truncated: false,
    markers_cap: 500,
    coverage: {},
    incidents: [
      {
        id: "i1",
        service_name: "payment",
        rule_name: "Payment errors",
        severity: "warning",
        opened_at: "2026-11-09T02:15:00.000Z",
        resolved_at: null,
      },
    ],
    incidents_truncated: false,
    ...changes,
  };
}

const mounted: Root[] = [];
afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

async function render(value: DashboardContext): Promise<HTMLElement> {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const rootRoute = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () =>
      createElement(WindowStrip, { context: value, empty: "nothing.", now: Date.parse(END) }),
  });
  const finding = createRoute({
    getParentRoute: () => rootRoute,
    path: "/findings/$findingId",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page, finding]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => {
    root.render(createElement(RouterProvider, { router }));
  });
  return container;
}

const lines = (container: HTMLElement) =>
  [...container.querySelectorAll("li")].map((item) =>
    (item.textContent ?? "").replace(/\s+/g, " ").trim(),
  );

describe("the In this window strip", () => {
  it("leads with open findings, critical first, then closed, incidents and releases (FR47)", () => {
    expect(stripEntries(context()).map((entry) => entry.key)).toEqual([
      "f-f-critical",
      "f-f-warning",
      "f-f-closed",
      "i-i1",
      "r-pe2",
      "r-d1",
    ]);
  });

  it("names each finding as a link a keyboard reaches in one action (AC65)", async () => {
    const container = await render(context());
    const links = [...container.querySelectorAll("a")];

    expect(links[0]?.getAttribute("href")).toBe("/findings/f-critical");
    expect(links[0]?.textContent).toBe("checkout is slower since 02:12");
    expect(lines(container)[0]).toBe("Critical checkout is slower since 02:12");
    expect(lines(container)[2]).toMatch(/ended 02:40 \S+/);
  });

  it("says a version only seen in telemetry so, never a deploy, with its escapes (AC122, FR61)", async () => {
    const container = await render(context({ findings: [] }));
    const said = lines(container);

    expect(said[2]).toMatch(/^checkout v2\.1 at 02:11 \S+/);
    expect(said[1]).toMatch(/^checkout v2\.2\[U\+202E\] first seen in telemetry 02:20 \S+/);
    expect(said.join(" ")).not.toMatch(/deploy/i);
    expect(said[0]).toMatch(/^Alert · Payment errors on payment since 02:15 \S+/);
  });

  it("says FR70's line only while a finding is listed (AC113)", async () => {
    expect((await render(context())).textContent).toContain(FINDINGS_DIFFER);
    expect((await render(context({ findings: [] }))).textContent).not.toContain(FINDINGS_DIFFER);
  });

  it("shows five and counts the rest, which one press opens", async () => {
    const many = context({
      findings: Array.from({ length: 7 }, (_, index) =>
        finding(`f-${index}`, "warning", `2026-11-09T02:${String(10 + index)}:00.000Z`),
      ),
      markers: [],
      incidents: [],
    });
    const container = await render(many);

    expect(lines(container)).toHaveLength(5);
    const more = container.querySelector("button");
    expect(more?.textContent).toBe("+2 more");
    await act(async () => more?.click());
    expect(lines(container)).toHaveLength(7);
  });

  it("says each cap that bit, so a busy window is never read as complete", () => {
    expect(
      truncationWords(
        context({ findings_truncated: true, incidents_truncated: true, markers_truncated: true }),
      ),
    ).toEqual([
      "Showing the newest 200 findings of this window.",
      "Showing the newest 50 alert incidents of this window.",
      "Showing the newest 500 releases of this window.",
    ]);
  });

  it("says what is empty in the words it is given, never a blank strip", async () => {
    const container = await render(context({ findings: [], markers: [], incidents: [] }));

    expect(container.textContent).toBe("In this window: nothing.");
  });
});
