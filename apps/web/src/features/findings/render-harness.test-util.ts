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
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId } from "@/shared/selection";

import type { FindingDetail } from "./api";
import { FindingScreen } from "./FindingScreen";
import { FindingsScreen } from "./FindingsScreen";

/**
 * The finding page mounted as the app mounts it, inside a `shell` layout route, with a
 * real query client and `fetch` answered from a table: only the network is replaced.
 */

export const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0021";
const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;

export const minutesAgo = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

export function finding(overrides: Partial<FindingDetail> = {}): FindingDetail {
  return {
    id: "f-1",
    project_id: PROJECT_ID,
    scope: "service",
    service_name: "checkout",
    title: "checkout is 3× slower than normal since 14:02 UTC",
    severity: "warning",
    state: "open",
    closed_reason: null,
    muted: false,
    started_at: minutesAgo(30),
    closed_at: null,
    acknowledged_at: null,
    signals: [
      {
        signal: "slower",
        normal: 110,
        current: 330,
        worst: 412,
        unit: "ms",
        comparison: "Tuesdays around 15:00, last 3 weeks",
        endpoint: null,
      },
    ],
    trend: [110, 330],
    series: [
      {
        signal: "slower",
        unit: "ms",
        points: [
          { at: minutesAgo(31), value: 112, normal: 110, edge: 165 },
          { at: minutesAgo(30), value: 330, normal: 110, edge: 165 },
          { at: minutesAgo(29), value: null, normal: 110, edge: 165 },
        ],
      },
    ],
    incidents: [],
    services: [],
    acknowledged_by: null,
    acknowledged_by_name: null,
    feedback_reason: null,
    new_normal: {
      available: true,
      readings: 30,
      needed: 15,
      signals: [{ signal: "slower", unit: "ms", ratio: 3, normal_before: 110, normal_after: 330 }],
    },
    ...overrides,
  };
}

export interface ApiCall {
  method: string;
  path: string;
  body: unknown;
}
type Handler = (call: ApiCall) => Response | Promise<Response>;

export const json =
  (body: unknown, status = 200): Handler =>
  () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export const problem =
  (status: number, detail: string): Handler =>
  () =>
    new Response(JSON.stringify({ type: "about:blank", title: "Refused", status, detail }), {
      status,
      headers: { "content-type": "application/problem+json" },
    });

/** The page's reads for `found`, as `role`, with any action stubs added. */
export function stubApi(
  found: FindingDetail | Handler,
  { role = "owner", extra = {} }: { role?: TestRole; extra?: Record<string, Handler> } = {},
) {
  const calls: ApiCall[] = [];
  const handlers: Record<string, Handler> = {
    "GET /auth/me": json(identityFor(role)),
    "GET /findings/f-1": typeof found === "function" ? found : json(found),
    [`GET /projects/${PROJECT_ID}/detection`]: json({ sensitivity: "normal", timezone: "UTC" }),
    ...extra,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      const call = {
        method,
        path,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      };
      calls.push(call);
      const handler = handlers[`${method} ${path}`];
      if (!handler) throw new Error(`no stub for ${method} ${path}`);
      return handler(call);
    }),
  );
  return {
    calls,
    to: (method: string, path: string) =>
      calls.filter((c) => c.method === method && c.path === path),
  };
}

const mounted: Root[] = [];

export function unmountAll() {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
}

export function mountFinding() {
  return mountAt("/findings/f-1", placeholder("Feed"));
}

/** The feed itself, at `/findings`, with each finding's page a placeholder. */
export function mountFeed() {
  return mountAt("/findings", FindingsScreen, placeholder("Finding"));
}

const placeholder = (text: string) => () => createElement("p", null, text);

async function mountAt(
  initial: string,
  feed: () => ReturnType<typeof createElement>,
  page: () => ReturnType<typeof createElement> = FindingScreen,
) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setSelectedProjectId(PROJECT_ID);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: Outlet });
  const shellRoute = createRoute({ getParentRoute: () => rootRoute, id: "shell" });
  const routeTree = rootRoute.addChildren([
    shellRoute.addChildren([
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/findings/$findingId",
        component: page,
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/findings",
        component: feed,
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/services/$name",
        component: placeholder("Service"),
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/alerts/incidents/$incidentId",
        component: placeholder("Incident"),
      }),
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
        // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
        createElement(RouterProvider, { router } as any),
      ),
    ),
  );
  await settle();
  return { container };
}

export async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

export async function waitFor<T>(find: () => T | null | undefined | false, what: string) {
  for (let round = 0; round < SETTLE_ROUNDS; round += 1) {
    const found = find();
    if (found) return found;
    await settle();
  }
  throw new Error(`gave up waiting for ${what}; the page reads: ${readable(document.body)}`);
}

export const readable = (element: Element) =>
  (element.textContent ?? "").replace(/\s+/g, " ").trim();

export function buttons(container: Element): string[] {
  return [...container.querySelectorAll("button")].map((each) => readable(each));
}

export async function press(container: Element, label: string) {
  const found = [...container.querySelectorAll("button")].find((each) => readable(each) === label);
  if (!found) throw new Error(`no button "${label}"; there are: ${buttons(container).join(", ")}`);
  await act(async () => found.click());
  await settle();
}
