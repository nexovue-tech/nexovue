import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  type RouteComponent,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import { parseFilterValues } from "@/shared/filters";
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId } from "@/shared/selection";
import type { CheckDefinition, CheckResultSummary, CheckRun } from "./api";
import { CheckDetailScreen, parseCheckSearch } from "./CheckDetailScreen";
import type { RunEvidence } from "./check-model";
import { SYNTHETIC_FILTERS } from "./filters";
import { NewCheckScreen, parseNewCheckSearch } from "./NewCheckScreen";
import { SyntheticsScreen } from "./SyntheticsScreen";

/**
 * The synthetics screens mounted as the app mounts them: inside a layout route with the
 * id `shell`, so `useSearch({ from: "/shell/synthetics" })` resolves, with a real query
 * client and `fetch` answered from a table. Only the network is replaced.
 */

export const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0018";

const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;

/* ------------------------------------------------------------------ time */

export const minutesAgo = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

/* ---------------------------------------------------------------- fixtures */

export function checkDefinition(overrides: Partial<CheckDefinition> = {}): CheckDefinition {
  return {
    id: "chk-api",
    project_id: PROJECT_ID,
    name: "Orders API",
    type: "api",
    config: {
      type: "api",
      request: {
        name: "",
        method: "GET",
        url: "https://api.example.com/orders",
        query: [],
        headers: [],
        body: { kind: "none" },
        auth: { kind: "none" },
        assertions: [
          { source: "status_code", property: "", comparison: "less_than", target: "400" },
        ],
        extractions: [],
        follow_redirects: true,
        timeout_seconds: 10,
      },
    },
    interval_seconds: 60,
    locations: [],
    degraded_ms: null,
    failing_ms: null,
    retries: 1,
    enabled: true,
    confirmed_state: "up",
    state_since: minutesAgo(90),
    last_run_at: minutesAgo(1),
    in_maintenance: false,
    displayed_state: "up",
    last_ping_at: null,
    created_at: minutesAgo(600),
    updated_at: minutesAgo(600),
    ...overrides,
  };
}

export function resultSummary(overrides: Partial<CheckResultSummary> = {}): CheckResultSummary {
  return {
    check_id: "chk-api",
    check_type: "api",
    runs: 60,
    successful_runs: 57,
    uptime_percent: 95,
    up: true,
    latest_outcome: "up",
    latest_status_code: 200,
    latest_run_at: minutesAgo(1),
    mean_duration_ms: 120,
    p95_duration_ms: 240,
    max_duration_ms: 400,
    tls: null,
    ...overrides,
  };
}

export function checkRun(overrides: Partial<CheckRun> = {}): CheckRun {
  return {
    timestamp: minutesAgo(1),
    run_id: "run-up",
    up: true,
    status_code: 200,
    duration_ms: 120,
    phase_total_ms: 118,
    outcome: "up",
    attempt: 1,
    final: true,
    maintenance: false,
    location: "mumbai",
    ...overrides,
  };
}

/** A run that got an answer and disagreed with one assertion about it. */
export function failedEvidence(overrides: Partial<RunEvidence> = {}): RunEvidence {
  return {
    run_id: "run-down",
    check_id: "chk-api",
    check_type: "api",
    location: "mumbai",
    attempt: 2,
    final: true,
    maintenance: false,
    outcome: "down",
    started_at: minutesAgo(3),
    duration_ms: 184,
    status_code: 200,
    tls_days_remaining: null,
    error: "1 of 2 assertions failed",
    steps: [
      {
        position: 0,
        name: "",
        target: "https://api.example.com/orders",
        duration_ms: 184,
        phases_ms: { dns: 4, tcp: 10, tls: 20, ttfb: 140, transfer: 10 },
        request: {
          method: "GET",
          url: "https://api.example.com/orders",
          headers: [{ name: "Accept", value: "application/json" }],
          body: null,
        },
        response: {
          status_code: 200,
          headers: [{ name: "Content-Type", value: "application/json" }],
          body: { text: '{"order": {"id": 41}}', size_bytes: 21, truncated: false, binary: false },
        },
        answers: [],
        assertions: [
          {
            source: "status_code",
            property: "",
            comparison: "less_than",
            target: "400",
            actual: "200",
            passed: true,
          },
          {
            source: "json_path",
            property: "$.order.id",
            comparison: "equals",
            target: "42",
            actual: "41",
            passed: false,
          },
        ],
        extracted: [],
        passed: false,
        error: null,
      },
    ],
    ...overrides,
  };
}

/* ------------------------------------------------------------------- the API */

export interface ApiCall {
  method: string;
  /** After `/api/v1`, e.g. `/checks/chk-api`. */
  path: string;
  search: URLSearchParams;
  body: unknown;
}

export type Handler = (call: ApiCall) => Response | Promise<Response>;

/** Keyed by method and path: `"GET /checks"`. */
export type Handlers = Record<string, Handler>;

export const json =
  (body: unknown, status = 200): Handler =>
  () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export const problem =
  (status: number, detail: string, extensions: Record<string, unknown> = {}): Handler =>
  () =>
    new Response(
      JSON.stringify({ type: "about:blank", title: "Refused", status, detail, ...extensions }),
      { status, headers: { "content-type": "application/problem+json" } },
    );

export const noContent: Handler = () => new Response(null, { status: 204 });

/** An answer that arrives only when the test says so. */
export function held(answer: Handler): { handler: Handler; release: () => void } {
  let release = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  return {
    handler: async (call) => {
      await released;
      return answer(call);
    },
    release,
  };
}

export const never: Handler = () => new Promise<Response>(() => {});

/** Every read the three screens make, answered from the given fixtures. */
export function syntheticsReads({
  checks = [],
  results = [],
  runs = {},
  evidence = [],
  availableLocations = ["mumbai", "hyderabad"],
}: {
  checks?: CheckDefinition[];
  results?: CheckResultSummary[];
  runs?: Record<string, CheckRun[]>;
  evidence?: RunEvidence[];
  availableLocations?: string[];
} = {}): Handlers {
  const handlers: Handlers = {
    "GET /checks": json({ checks, available_locations: availableLocations }),
    "GET /synthetics/checks": json({ checks: results }),
    "GET /synthetics/checks/trend": json({
      window_start: minutesAgo(60),
      window_end: minutesAgo(0),
      bucket_seconds: 60,
      checks: [],
    }),
    [`GET /projects/${PROJECT_ID}/check-secrets`]: json({ secrets: [] }),
    [`GET /projects/${PROJECT_ID}/check-variables`]: json({ variables: [] }),
  };
  for (const check of checks) {
    handlers[`GET /checks/${check.id}`] = json(check);
    handlers[`GET /checks/${check.id}/state-changes`] = json({ check_id: check.id, changes: [] });
    handlers[`GET /alerts/coverage/checks/${check.id}`] = json({ rules: [], open_incidents: [] });
    handlers[`GET /synthetics/checks/${check.id}/runs`] = json({
      check_id: check.id,
      runs: runs[check.id] ?? [],
    });
    handlers[`GET /synthetics/checks/${check.id}/phases`] = json({
      check_id: check.id,
      bucket_seconds: 60,
      phases: [],
      buckets: [],
    });
  }
  for (const run of evidence) {
    handlers[`GET /synthetics/checks/${run.check_id}/runs/${run.run_id}`] = json({
      evidence: run,
    });
  }
  return handlers;
}

export interface StubbedApi {
  calls: ApiCall[];
  /** Calls to one method and path, in order. */
  to: (method: string, path: string) => ApiCall[];
  /** Requests no handler answered: each one is a read the test did not expect. */
  unanswered: string[];
}

export function stubApi(handlers: Handlers): StubbedApi {
  const calls: ApiCall[] = [];
  const unanswered: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.startsWith(API_PREFIX)
        ? url.pathname.slice(API_PREFIX.length)
        : url.pathname;
      const call: ApiCall = {
        method,
        path,
        search: url.searchParams,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      };
      calls.push(call);
      // Everyone is an owner unless a test says otherwise (F26: screens hide by role).
      const handler =
        handlers[`${method} ${path}`] ??
        (`${method} ${path}` === "GET /auth/me" ? json(identityFor("owner")) : undefined);
      if (!handler) {
        unanswered.push(`${method} ${path}`);
        throw new Error(`no stub for ${method} ${path}`);
      }
      return handler(call);
    }),
  );
  return {
    calls,
    unanswered,
    to: (method, path) => calls.filter((call) => call.method === method && call.path === path),
  };
}

/* ------------------------------------------------------------------ mounting */

const mounted: Root[] = [];

export function unmountAll() {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
}

const Placeholder = (text: string) => () => createElement("p", null, text);

/**
 * Mount the synthetics routes at `url`. `harness` puts any component at `/harness`, for
 * a part of a screen that needs the router and the API but not the whole screen.
 */
export async function mountAt(url: string, harness?: RouteComponent) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setSelectedProjectId(PROJECT_ID);

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: Outlet });
  const shellRoute = createRoute({ getParentRoute: () => rootRoute, id: "shell" });
  const under = (path: string, component: RouteComponent) =>
    createRoute({ getParentRoute: () => shellRoute, path, component });

  const routeTree = rootRoute.addChildren([
    shellRoute.addChildren([
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/synthetics",
        component: SyntheticsScreen,
        validateSearch: (search: Record<string, unknown>) =>
          parseFilterValues(SYNTHETIC_FILTERS, search),
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/synthetics/new",
        component: NewCheckScreen,
        validateSearch: parseNewCheckSearch,
      }),
      under("/synthetics/import", Placeholder("Import screen")),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/synthetics/$checkId",
        component: CheckDetailScreen,
        validateSearch: parseCheckSearch,
      }),
      under("/settings/projects", Placeholder("Project settings")),
      under("/harness", harness ?? Placeholder("Nothing mounted")),
    ]),
  ]);
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [url] }),
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
  return { container, router, client };
}

/* --------------------------------------------------------------- interaction */

export async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** Lets fetches, query updates and lazy imports land until `find` succeeds. */
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

export function buttonLabelled(container: Element, label: string): HTMLButtonElement {
  const found = findButton(container, label);
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

export function findButton(container: Element, label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (candidate) =>
      candidate.textContent?.trim() === label || candidate.getAttribute("aria-label") === label,
  );
}

export function inputLabelled(container: Element, label: string): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!input) throw new Error(`no input labelled "${label}"`);
  return input;
}

export async function click(element: Element) {
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
}

export async function typeInto(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

export async function choose(select: HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
}

export async function submitForm(container: Element) {
  const form = container.querySelector("form");
  if (!form) throw new Error("no form on the screen");
  await act(async () => form.requestSubmit());
  await settle();
}

/** The error text an input is described by, or undefined when it has none. */
export function describedError(input: Element): string | undefined {
  const id = input.getAttribute("aria-describedby");
  if (!id || input.getAttribute("aria-invalid") !== "true") return undefined;
  return document.getElementById(id)?.textContent ?? undefined;
}
