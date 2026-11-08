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

import type { SloList, SloMonth, SloMonthRow, SloMonths, SloView } from "./api";
import { EditSloScreen, NewSloScreen } from "./SloForm";
import { SloScreen } from "./SloScreen";
import { SlosScreen } from "./SlosScreen";

/** The reliability target screens with a real router and query client; only `fetch` is a table. */

export const PROJECT_ID = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;

export const minutesAgo = (minutes: number, from: number = Date.now()) =>
  new Date(from - minutes * 60_000).toISOString();

export function target(overrides: Partial<SloView> = {}): SloView {
  return {
    id: "slo-1",
    project_id: PROJECT_ID,
    name: "checkout availability",
    description: "",
    kind: "service_availability",
    service_name: "checkout",
    endpoint_name: null,
    check_id: null,
    check_name: null,
    check_interval_seconds: null,
    boundary_index: null,
    boundary_ms: null,
    asked_ms: null,
    left_out_endpoints: [],
    objective_percent: "99.90",
    window_days: 28,
    upstream_sampling: "no",
    enabled: true,
    label: "checkout availability, 99.9% over 28 days",
    allowance_words: "Failures still allowed",
    state: "on_track",
    variant: "plain",
    qualifiers: [],
    attention: false,
    severity: null,
    hours_left: null,
    quiet_minutes: null,
    figures: {
      good: 671_688,
      total: 672_000,
      failures: 312,
      allowed: 672,
      left: 360,
      missed_by: 0,
      figure_percent: "99.95",
      coverage: 0.99,
      lost_minutes: 0,
      excluded_minutes: 0,
      days_held: 28,
    },
    evaluated_minute: minutesAgo(1),
    last_event_minute: minutesAgo(1),
    longest_quiet_7d_minutes: 0,
    zone: "UTC",
    created_at: minutesAgo(60 * 24 * 30),
    updated_at: minutesAgo(60 * 24 * 30),
    ...overrides,
  };
}

/** A whole October, met at 99.95% against 99.9%, every minute measured. */
export function month(overrides: Partial<SloMonth> = {}): SloMonth {
  return {
    month: "2026-10-01",
    result: "met",
    figure_percent: "99.95",
    objective_percent: "99.90",
    good: 309_845,
    total: 310_000,
    failures: 155,
    coverage: 1,
    quiet_minutes: 0,
    longest_quiet_minutes: 0,
    lost_minutes: 0,
    excluded_minutes: 0,
    missed_runs: null,
    maintenance_runs: null,
    days_held: 31,
    days_in_month: 31,
    may_still_change: false,
    loss_known: true,
    changed_at: null,
    changed_why: null,
    earlier_definition_through: null,
    sharp_falls: [],
    outages: [],
    time_not_counted: false,
    ...overrides,
  };
}

export const REQUEST_LABEL =
  "Measured from requests that reached the service and were reported. Not a contract figure.";
export const AUDIENCE = "For your own team and board, not for customer promises.";

export function monthsOf(months: SloMonth[], overrides: Partial<SloMonths> = {}): SloMonths {
  return {
    id: "slo-1",
    label: "checkout availability, 99.9% over 28 days",
    kind: "service_availability",
    upstream_sampling: "no",
    report_label: REQUEST_LABEL,
    audience: AUDIENCE,
    zone: "UTC",
    months,
    ...overrides,
  };
}

export function monthRow(overrides: Partial<SloMonthRow> = {}): SloMonthRow {
  return {
    ...month(),
    id: "slo-1",
    name: "checkout availability",
    label: "checkout availability, 99.9% over 28 days",
    kind: "service_availability",
    upstream_sampling: "no",
    report_label: REQUEST_LABEL,
    deleted_at: null,
    ...overrides,
  };
}

export function list(targets: SloView[], overrides: Partial<SloList> = {}): SloList {
  return { targets, deleted: [], zone: "UTC", has_services: true, ...overrides };
}

type Handler = () => Response;
export const json =
  (body: unknown, status = 200): Handler =>
  () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export function stubApi(handlers: Record<string, Handler>, role: TestRole = "owner") {
  const calls: Array<{ method: string; path: string; body: unknown }> = [];
  const all: Record<string, Handler> = {
    "GET /auth/me": json(identityFor(role)),
    // A target's page opens on its months; a test about the rest need not say so.
    "GET /slos/slo-1/months": json(monthsOf([])),
    "GET /slos/slo-1/history": json({ entries: [] }),
    ...handlers,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({
        method,
        path,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      const handler = all[`${method} ${path}`];
      if (!handler) throw new Error(`no stub for ${method} ${path}`);
      return handler();
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
    defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: Outlet });
  const shell = createRoute({ getParentRoute: () => rootRoute, id: "shell" });
  const at = (path: string, component: () => ReturnType<typeof createElement>) =>
    createRoute({ getParentRoute: () => shell, path, component });
  const routeTree = rootRoute.addChildren([
    shell.addChildren([
      at("/slos", SlosScreen),
      at("/slos/new", NewSloScreen),
      at("/slos/$sloId", SloScreen),
      at("/slos/$sloId/edit", EditSloScreen),
      at("/settings/general", () => createElement("p", null, "Settings")),
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

export function buttons(container: Element): string[] {
  return [...container.querySelectorAll("button")].map((each) => readable(each));
}

export async function press(container: Element, label: string) {
  const found = [...container.querySelectorAll("button")].find((each) => readable(each) === label);
  if (!found) throw new Error(`no button "${label}"; there are: ${buttons(container).join(", ")}`);
  await act(async () => found.click());
  await settle();
}
