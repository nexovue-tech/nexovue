import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  type AnyRouter,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import type { ReactElement } from "react";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";

/**
 * One dashboards component mounted with a real router and query client, `fetch` answered
 * from a function, and a pinned clock. Only the network is replaced. A screen's links
 * resolve against the dashboard routes, so they render as the app renders them.
 */

export const API_PREFIX = "/api/v1";
/** Pinned: relative words and "not opened for 30 days" count from this. */
export const NOW = Date.parse("2026-11-12T10:00:00Z");
export const DAY_MS = 86_400_000;
const SETTLE_ROUNDS = 40;

export interface Request {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

export type Answer = Response | undefined;

export const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export const problem = (status: number, detail: string, extra: Record<string, unknown> = {}) =>
  new Response(
    JSON.stringify({
      type: "https://nexovue.com/problems/conflict",
      title: "x",
      status,
      detail,
      ...extra,
    }),
    { status, headers: { "content-type": "application/problem+json" } },
  );

export const text = (node: Element | null | undefined) =>
  (node?.textContent ?? "").replace(/\s+/g, " ").trim();

const mounted: Root[] = [];

export function unmountAll(): void {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
}

export async function settle(rounds = SETTLE_ROUNDS): Promise<void> {
  for (let round = 0; round < rounds; round++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

export async function mountScreen(
  element: ReactElement,
  answer: (request: Request) => Answer,
  role: TestRole = "member",
): Promise<{ container: HTMLElement; calls: Request[]; router: AnyRouter }> {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const calls: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const path = url.pathname.slice(API_PREFIX.length);
      if (path === "/auth/me") return json(identityFor(role));
      const request: Request = {
        method: init?.method ?? "GET",
        path,
        search: url.search,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      };
      calls.push(request);
      return answer(request) ?? new Response(null, { status: 204 });
    }),
  );
  const root$ = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root$, path: "/", component: () => element });
  const links = ["/dashboards", "/dashboards/new", "/dashboards/$dashboardId"].map((path) =>
    createRoute({ getParentRoute: () => root$, path, component: () => null }),
  );
  const router = createRouter({
    routeTree: root$.addChildren([page, ...links]),
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
  await settle();
  return { container, calls, router };
}

export function typeInto(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

export const buttonNamed = (container: Element, name: string) =>
  [...container.querySelectorAll("button")].find((each) => text(each) === name);
