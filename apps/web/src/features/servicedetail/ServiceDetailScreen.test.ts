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

import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId, setSelectedRangeId } from "@/shared/selection";

import { ServiceDetailScreen } from "./ServiceDetailScreen";

/**
 * A service's screen starts *Service health* from itself (F20, T9.2; FR41, AC38, AC138):
 * an editor gets "Start a dashboard from this service", a link that opens the template as
 * a draft with the picker on the service and saves nothing; once *Service health* exists,
 * everyone reads "Open Service health for checkout" and goes straight there, and no second
 * copy is offered. A viewer is never offered a start. The screen's other reads are
 * answered "not found": only this offer is under test here.
 */

const API_PREFIX = "/api/v1";
const PROJECT = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const NOW = Date.parse("2026-11-12T10:00:00Z");
const SETTLE_ROUNDS = 30;

const TEMPLATES = {
  templates: [
    { id: "start_from_overview", name: "Start from Overview", document: { title: "Overview" } },
    { id: "service_health", name: "Service health", document: { title: "Service health" } },
    { id: "infrastructure", name: "Infrastructure", document: { title: "Infrastructure" } },
  ],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const notFound = () =>
  new Response(JSON.stringify({ type: "about:blank", title: "Not found", status: 404 }), {
    status: 404,
    headers: { "content-type": "application/problem+json" },
  });

const mounted: Root[] = [];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function mount(dashboards: { id: string; title: string }[], role: TestRole = "member") {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setSelectedProjectId(PROJECT);
  setSelectedRangeId("1h");
  const calls: { method: string; path: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path });
      if (path === "/auth/me") return json(identityFor(role));
      if (path === "/dashboards/templates") return json(TEMPLATES);
      if (path === "/dashboards") return json({ dashboards, more: false });
      return notFound();
    }),
  );
  const root$ = createRootRoute({ component: Outlet });
  const page = createRoute({
    getParentRoute: () => root$,
    path: "/",
    component: () => createElement(ServiceDetailScreen, { serviceName: "checkout" }),
  });
  const targets = ["/dashboards", "/dashboards/new", "/dashboards/$dashboardId"].map((path) =>
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
  for (let round = 0; round < SETTLE_ROUNDS; round++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return { container, calls };
}

const links = (container: Element) =>
  [...container.querySelectorAll("a")].map((link) => [
    (link.textContent ?? "").trim(),
    link.getAttribute("href"),
  ]);
const offered = (container: Element, words: string) =>
  links(container).filter(
    ([label]) => label === words || label?.toLowerCase().startsWith("open service health"),
  );

describe("starting a dashboard from a service (FR41)", () => {
  it("offers an editor Service health as a draft with the picker on this service, saving nothing (AC38)", async () => {
    const { container, calls } = await mount([{ id: "d-1", title: "Checkout on-call" }]);

    expect(offered(container, "Start a dashboard from this service")).toEqual([
      [
        "Start a dashboard from this service",
        "/dashboards/new?template=service_health&service=checkout",
      ],
    ]);
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("once Service health exists, opens it for this service and offers no second copy (AC138)", async () => {
    const { container } = await mount([
      { id: "d-1", title: "Checkout on-call" },
      { id: "sh-1", title: "Service health" },
    ]);

    expect(offered(container, "Start a dashboard from this service")).toEqual([
      ["Open Service health for checkout", `/dashboards/sh-1?project=${PROJECT}&service=checkout`],
    ]);
  });

  it("offers a viewer no start, and still the one there is", async () => {
    const none = await mount([], "viewer");
    expect(offered(none.container, "Start a dashboard from this service")).toEqual([]);

    const saved = await mount([{ id: "sh-1", title: "service health" }], "viewer");
    expect(offered(saved.container, "Start a dashboard from this service")).toEqual([
      ["Open service health for checkout", `/dashboards/sh-1?project=${PROJECT}&service=checkout`],
    ]);
  });
});
