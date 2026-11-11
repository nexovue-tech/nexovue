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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearScreenOverride,
  readRememberedSelection,
  readSelection,
  setSelectedProjectId,
  setSelectedRangeId,
} from "@/shared/selection";

import { exactTimesFirst, exactTimesLink, liveLink } from "./copy-link";
import {
  addressProject,
  addressWindow,
  inDisplayMode,
  parseDashboardSearch,
} from "./dashboard-search";
import type { DashboardAddress } from "./use-dashboard-address";
import { useDashboardAddress } from "./use-dashboard-address";

/**
 * A dashboard's address (F20, FR28, FR53; T6.1, T6.2; AC27, AC28, AC69, AC103): what
 * `validateSearch` keeps, and the hook that makes the address win for this page without
 * the reader's own choice ever being written. A fixed date is set where a window is read.
 */

const A = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const B = "7b2e1d3f-4c5a-4b6e-8f9a-8d7c6b5a4e3f";
const FROM = "2026-11-10T02:00:00.000Z";
const TO = "2026-11-10T03:00:00.000Z";

describe("what the address may carry (T6.1)", () => {
  it("keeps a project, a range and a service that are well formed", () => {
    expect(
      parseDashboardSearch({ project: B.toUpperCase(), range: "30d", service: "checkout" }),
    ).toEqual({
      project: B,
      range: "30d",
      service: "checkout",
    });
  });

  it("drops what the API would refuse: a bad project, an unknown range, a window with no length", () => {
    expect(parseDashboardSearch({ project: "production", range: "2y" })).toEqual({});
    expect(parseDashboardSearch({ from: TO, to: FROM })).toEqual({});
    expect(parseDashboardSearch({ from: FROM, to: "2026-12-31T00:00:00.000Z" })).toEqual({});
  });

  it("prefers fixed times to a range, since a link naming both was made by hand", () => {
    expect(parseDashboardSearch({ from: FROM, to: TO, range: "1h" })).toEqual({
      from: FROM,
      to: TO,
    });
  });

  it("reads full screen from a number, as the router parses it, or from text", () => {
    expect(parseDashboardSearch({ display: 1 }).display).toBe(1);
    expect(parseDashboardSearch({ display: "1" }).display).toBe(1);
    expect(parseDashboardSearch({ display: 2 }).display).toBeUndefined();
  });

  it("refuses a service too long to be a name", () => {
    expect(parseDashboardSearch({ service: "x".repeat(256) })).toEqual({});
  });

  it("applies the dashboard's own range only when the address names no window (FR52)", () => {
    expect(addressWindow({}, "6h")).toEqual({ kind: "relative", rangeId: "6h" });
    expect(addressWindow({ range: "15m" }, "6h")).toEqual({ kind: "relative", rangeId: "15m" });
    expect(addressWindow({}, null)).toBeUndefined();
    expect(addressWindow({ from: FROM, to: TO }, "6h")).toEqual({
      kind: "absolute",
      from: FROM,
      to: TO,
    });
  });

  it("holds a project back until the list has loaded, and refuses one that is not in it (AC27, AC69)", () => {
    expect(addressProject({ project: B }, undefined, false)).toEqual({ state: "loading" });
    expect(addressProject({ project: B }, undefined, true)).toEqual({ state: "unchecked" });
    expect(addressProject({ project: B }, [{ id: A }], false)).toEqual({ state: "foreign", id: B });
    expect(addressProject({ project: B }, [{ id: A }, { id: B }], false)).toEqual({
      state: "ready",
      id: B,
    });
    expect(addressProject({}, [{ id: A }], false)).toEqual({ state: "none" });
  });

  it("recognises a dashboard in full screen, and not the builder or the new page", () => {
    expect(inDisplayMode("/dashboards/abc", { display: 1 })).toBe(true);
    expect(inDisplayMode("/dashboards/abc", {})).toBe(false);
    expect(inDisplayMode("/dashboards/new", { display: 1 })).toBe(false);
    expect(inDisplayMode("/dashboards/abc/edit", { display: 1 })).toBe(false);
    expect(inDisplayMode("/services/abc", { display: 1 })).toBe(false);
  });
});

describe("the links a dashboard copies (AC137)", () => {
  const base = {
    origin: "https://app.example",
    pathname: "/dashboards/d1",
    stringify: (search: Record<string, unknown>) =>
      `?${new URLSearchParams(search as Record<string, string>).toString()}`,
    projectId: B,
    service: "checkout",
  };

  it("names the exact minutes, the project and the service", () => {
    const url = new URL(exactTimesLink(base, { from: FROM, to: TO }));
    expect(Object.fromEntries(url.searchParams)).toEqual({
      project: B,
      from: FROM,
      to: TO,
      service: "checkout",
    });
  });

  it("names a live range instead, with no times", () => {
    const url = new URL(liveLink(base, "1h"));
    expect(Object.fromEntries(url.searchParams)).toEqual({
      project: B,
      range: "1h",
      service: "checkout",
    });
  });

  it("puts exact times first after a drag or on a fixed window", () => {
    expect(exactTimesFirst({ kind: "absolute", from: FROM, to: TO })).toBe(true);
    expect(exactTimesFirst({ kind: "relative", rangeId: "1h" })).toBe(false);
  });
});

let root: Root | undefined;
let address: DashboardAddress | undefined;
/** What the tests use of the router, which the generic type will not let a variable hold. */
interface Navigation {
  history: { length: number; location: { search: string } };
  navigate: (options: { to: string }) => Promise<void>;
}
let router: Navigation | undefined;

function Probe() {
  address = useDashboardAddress({ defaultRange: null, zone: undefined });
  return null;
}

async function mountAt(initial: string, projects: string[] = [A, B]) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            projects: projects.map((id) => ({ id, slug: id, name: id, first_reported_on: null })),
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    ),
  );
  const rootRoute = createRootRoute({ component: Outlet });
  const view = createRoute({
    getParentRoute: () => rootRoute,
    path: "/dashboards/$dashboardId",
    component: Probe,
  });
  const other = createRoute({
    getParentRoute: () => rootRoute,
    path: "/overview",
    component: () => null,
  });
  const created = createRouter({
    routeTree: rootRoute.addChildren([view, other]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  router = created;
  root = createRoot(document.createElement("div"));
  await act(async () => {
    root?.render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        createElement(RouterProvider, { router: created }),
      ),
    );
  });
  await settle();
}

const settle = () => act(async () => new Promise((resolve) => setTimeout(resolve, 30)));

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-10T12:00:00.000Z"));
  setSelectedProjectId(A);
  setSelectedRangeId("1h");
});

afterEach(() => {
  if (root) act(() => root?.unmount());
  root = undefined;
  clearScreenOverride();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the address wins for this page (T6.2)", () => {
  it("shows project B to a reader on project A, and no read may run before they agree (AC27)", async () => {
    await mountAt(`/dashboards/d1?project=${B}&range=6h`);
    expect(readSelection().projectId).toBe(B);
    expect(readSelection().window).toEqual({ kind: "relative", rangeId: "6h" });
    expect(address?.ready).toBe(true);
    expect(address?.fromLink).toBe(true);
  });

  it("is not ready, and has changed nothing, while the project list has not answered", async () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    const rootRoute = createRootRoute({ component: Outlet });
    const view = createRoute({
      getParentRoute: () => rootRoute,
      path: "/dashboards/$dashboardId",
      component: Probe,
    });
    const created = createRouter({
      routeTree: rootRoute.addChildren([view]),
      history: createMemoryHistory({ initialEntries: [`/dashboards/d1?project=${B}`] }),
    });
    router = created;
    root = createRoot(document.createElement("div"));
    await act(async () => {
      root?.render(
        createElement(
          QueryClientProvider,
          { client: new QueryClient() },
          createElement(RouterProvider, { router: created }),
        ),
      );
    });
    expect(address?.ready).toBe(false);
    expect(readSelection().projectId).toBe(A);
  });

  it("refuses a project the reader cannot open, keeps the address, and leaves the stored choice alone (AC69)", async () => {
    await mountAt(`/dashboards/d1?project=${B}`, [A]);
    expect(address?.project).toEqual({ state: "foreign", id: B });
    expect(address?.ready).toBe(false);
    expect(readSelection().projectId).toBe(A);
    expect(router?.history.location.search).toContain(B);
    expect(readRememberedSelection().projectId).toBe(A);
  });

  it("leaves the reader's own project and window when the page is left (AC69)", async () => {
    await mountAt(`/dashboards/d1?project=${B}&from=${FROM}&to=${TO}`);
    expect(readSelection().window.kind).toBe("absolute");
    await act(async () => router?.navigate({ to: "/overview" }));
    act(() => root?.unmount());
    root = undefined;
    // No manual clearing: unmounting the page is what ends its override.
    expect(readSelection().projectId).toBe(A);
    expect(readSelection().window).toEqual({ kind: "relative", rangeId: "1h" });
    expect(readRememberedSelection().projectId).toBe(A);
    expect(readRememberedSelection().window).toEqual({ kind: "relative", rangeId: "1h" });
  });

  it("keeps a fixed link's window fixed, with nothing to refresh (AC28)", async () => {
    await mountAt(`/dashboards/d1?from=${FROM}&to=${TO}`);
    expect(address?.choice).toEqual({ kind: "absolute", from: FROM, to: TO });
  });

  it("pushes one history entry for a drag, and browser Back restores the link's window (AC103)", async () => {
    await mountAt(`/dashboards/d1?range=6h`);
    const before = router?.history.length ?? 0;
    await act(async () => address?.zoom({ from: FROM, to: TO }));
    await settle();
    expect(router?.history.length).toBe(before + 1);
    expect(address?.choice).toEqual({ kind: "absolute", from: FROM, to: TO });
    expect(address?.zoomedFrom).toEqual({ kind: "relative", rangeId: "6h" });
    // A drag is never remembered: the reader's own window is as it was.
    expect(readRememberedSelection().window).toEqual({ kind: "relative", rangeId: "1h" });

    await act(async () => address?.backFromZoom());
    await settle();
    expect(address?.choice).toEqual({ kind: "relative", rangeId: "6h" });
    expect(address?.zoomedFrom).toBeUndefined();
  });

  it("replaces the entry for any other change of window", async () => {
    await mountAt(`/dashboards/d1?range=6h`);
    const before = router?.history.length ?? 0;
    await act(async () => address?.replaceWindow({ kind: "relative", rangeId: "24h" }));
    await settle();
    expect(router?.history.length).toBe(before);
    expect(address?.choice).toEqual({ kind: "relative", rangeId: "24h" });
  });

  it("follows the reader's own choice in the top bar, which is remembered as anywhere", async () => {
    await mountAt(`/dashboards/d1?project=${B}&range=6h`);
    await act(async () => setSelectedRangeId("24h"));
    await settle();
    expect(router?.history.location.search).toContain("24h");
    expect(readRememberedSelection().window).toEqual({ kind: "relative", rangeId: "24h" });
    expect(readSelection().projectId).toBe(B);
  });

  it("returns to the reader's own project from a link", async () => {
    await mountAt(`/dashboards/d1?project=${B}`);
    await act(async () => address?.backToOwnProject());
    await settle();
    expect(readSelection().projectId).toBe(A);
    expect(router?.history.location.search).not.toContain(B);
  });
});
