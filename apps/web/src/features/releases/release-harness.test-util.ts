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
import { vi } from "vitest";
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId } from "@/shared/selection";

import type { ReleaseList, ReleaseRow } from "./api";
import { ReleaseScreen } from "./ReleaseScreen";
import { ReleasesScreen } from "./ReleasesScreen";

/** The release screens with a real router and query client; only `fetch` is a table. */

export const PROJECT_ID = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;

export const minutesAgo = (minutes: number, from: number = Date.now()) =>
  new Date(from - minutes * 60_000).toISOString();

export function row(overrides: Partial<ReleaseRow> = {}): ReleaseRow {
  return {
    id: "release-1",
    period_id: "period-1",
    service_name: "checkout",
    version: "v2.1",
    started_at: minutesAgo(20),
    ended_at: null,
    start_basis: "seen",
    basis: "seen",
    activity: "serving",
    already_running: false,
    is_return: false,
    last_seen_at: minutesAgo(1),
    last_state: null,
    ...overrides,
  };
}

export function listOf(rows: ReleaseRow[], overrides: Partial<ReleaseList> = {}): ReleaseList {
  return {
    releases: rows,
    total: rows.length,
    services: new Set(rows.map((each) => each.service_name)).size,
    next_cursor: null,
    has_any: rows.length > 0,
    latest: null,
    window_from: minutesAgo(60),
    window_to: minutesAgo(0),
    ...overrides,
  };
}

type Handler = (url: URL) => Response;
export const json =
  (body: unknown, status = 200): Handler =>
  () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export const PROJECTS = json({
  projects: [{ id: PROJECT_ID, slug: "production", name: "production", first_reported_on: null }],
});

export function stubApi(handlers: Record<string, Handler>) {
  const calls: URL[] = [];
  const all: Record<string, Handler> = {
    "GET /auth/me": json(identityFor("owner")),
    "GET /projects": PROJECTS,
    [`GET /projects/${PROJECT_ID}/detection`]: json({ timezone: "UTC" }),
    ...handlers,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push(url);
      const handler = all[`${method} ${path}`];
      if (!handler) throw new Error(`no stub for ${method} ${path}`);
      return handler(url);
    }),
  );
  return calls;
}

const mounted: Root[] = [];

export function unmountAll() {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
}

export async function mountAt(initial: string) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setSelectedProjectId(PROJECT_ID);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
  });
  const rootRoute = createRootRoute({ component: Outlet });
  const shell = createRoute({ getParentRoute: () => rootRoute, id: "shell" });
  const at = (path: string, component: () => ReturnType<typeof createElement>) =>
    createRoute({ getParentRoute: () => shell, path, component });
  const routeTree = rootRoute.addChildren([
    shell.addChildren([
      at("/releases", ReleasesScreen),
      at("/releases/$releaseId", ReleaseScreen),
      at("/docs", () => createElement("p", null, "Docs")),
    ]),
  ]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(RouterProvider, { router } as never),
      ),
    ),
  );
  await settle();
  return { container, router };
}

export async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

export const readable = (element: Element) =>
  (element.textContent ?? "").replace(/\s+/g, " ").trim();

export async function waitFor<T>(find: () => T | null | undefined | false, what: string) {
  for (let round = 0; round < SETTLE_ROUNDS; round += 1) {
    const found = find();
    if (found) return found;
    await settle();
  }
  throw new Error(`gave up waiting for ${what}; the page reads: ${readable(document.body)}`);
}

export async function press(container: Element, label: string) {
  const found = [...container.querySelectorAll("button")].find((each) => readable(each) === label);
  if (!found) throw new Error(`no button "${label}"`);
  await act(async () => found.click());
  await settle();
}
