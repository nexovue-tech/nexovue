// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OpenWindow } from "../dashboards-for-service";
import { DashboardsShowingThisService } from "./DashboardsShowingThisService";

/**
 * "Dashboards showing this service" (F20, T7.8; FR57, AC82): naming ones first as the API
 * orders them, at most five with "See all", each opening with the service picked in the
 * project, on the window it was given; each state said.
 */

const PROJECT = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function mount(
  answer: () => Response | Promise<Response>,
  window: OpenWindow | "none" = { kind: "live", rangeId: "1h" },
  service = "checkout",
) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      requests.push(url.pathname + url.search);
      return answer();
    }),
  );
  const root$ = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root$,
    path: "/",
    component: () =>
      createElement(DashboardsShowingThisService, {
        service,
        projectId: PROJECT,
        window: window === "none" ? undefined : window,
      }),
  });
  const targets = ["/dashboards", "/dashboards/$dashboardId"].map((path) =>
    createRoute({ getParentRoute: () => root$, path, component: () => null }),
  );
  const router = createRouter({
    routeTree: root$.addChildren([page, ...targets]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      createElement(QueryClientProvider, { client }, createElement(RouterProvider, { router })),
    );
  });
  for (let round = 0; round < 20; round++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return { container, requests };
}

const text = (node: Element) => (node.textContent ?? "").replace(/\s+/g, " ").trim();
const rows = (title: string, count: number) =>
  Array.from({ length: count }, (_, index) => ({
    id: `d${index}`,
    title: `${title} ${index}`,
    uses_picker: index > 0,
  }));

describe("the list", () => {
  it("asks for this service's dashboards and lists them in the API's order", async () => {
    const { container, requests } = await mount(() =>
      json({ dashboards: rows("Board", 3), more: false }),
    );

    expect(requests).toEqual(["/api/v1/dashboards?service=checkout"]);
    expect([...container.querySelectorAll("li")].map((each) => text(each))).toEqual([
      "Board 0",
      "Board 1",
      "Board 2",
    ]);
    expect(text(container)).not.toContain("See all");
    expect(container.querySelector("h2")?.textContent).toBe("Dashboards showing this service");
  });

  it("opens each with the service and project in the address, on a live range", async () => {
    const { container } = await mount(() => json({ dashboards: rows("Board", 1), more: false }));

    const href = container.querySelector("a")?.getAttribute("href") ?? "";
    expect(href.startsWith("/dashboards/d0?")).toBe(true);
    const search = new URLSearchParams(href.split("?")[1]);
    expect(Object.fromEntries(search)).toEqual({
      project: PROJECT,
      service: "checkout",
      range: "1h",
    });
  });

  it("opens on fixed times when it was given them", async () => {
    const { container } = await mount(() => json({ dashboards: rows("Board", 1), more: false }), {
      kind: "fixed",
      from: "2026-11-12T08:00:00.000Z",
      to: "2026-11-12T09:00:00.000Z",
    });

    const search = new URLSearchParams(
      container.querySelector("a")?.getAttribute("href")?.split("?")[1],
    );
    expect(search.get("from")).toBe("2026-11-12T08:00:00.000Z");
    expect(search.get("to")).toBe("2026-11-12T09:00:00.000Z");
    expect(search.get("range")).toBeNull();
  });

  it("opens on the dashboard's own window when given none", async () => {
    const { container } = await mount(
      () => json({ dashboards: rows("Board", 1), more: false }),
      "none",
    );
    const search = new URLSearchParams(
      container.querySelector("a")?.getAttribute("href")?.split("?")[1],
    );
    expect([...search.keys()].sort()).toEqual(["project", "service"]);
  });

  it("offers See all when more exist than the five listed", async () => {
    const { container } = await mount(() => json({ dashboards: rows("Board", 5), more: true }));

    const seeAll = [...container.querySelectorAll("a")].find((each) => text(each) === "See all");
    expect(seeAll?.getAttribute("href")).toBe("/dashboards");
    expect(container.querySelectorAll("li")).toHaveLength(5);
  });

  it("shows a title with hidden characters as escapes (FR61)", async () => {
    const { container } = await mount(() =>
      json({ dashboards: [{ id: "d0", title: "Che‮ckout", uses_picker: false }], more: false }),
    );
    expect(text(container.querySelector("li") as Element)).toBe("Che[U+202E]ckout");
  });
});

describe("the other states", () => {
  it("says no dashboard shows the service yet", async () => {
    const { container } = await mount(() => json({ dashboards: [], more: false }));
    expect(text(container)).toContain("No dashboard shows checkout yet.");
  });

  it("says it could not list them, with a retry, and the rest of the page is unaffected", async () => {
    const { container } = await mount(() => json({ type: "x", title: "x", status: 500 }, 500));
    expect(text(container)).toContain("Could not list dashboards");
    expect([...container.querySelectorAll("button")].map((each) => text(each))).toEqual([
      "Try again",
    ]);
  });
});
