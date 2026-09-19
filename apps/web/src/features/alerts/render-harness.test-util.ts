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

import { setSelectedProjectId } from "@/shared/selection";

import { AlertsScreen } from "./AlertsScreen";
import type { AlertRule, ChannelSummary, Incident } from "./api";
import { ChannelDetailScreen } from "./ChannelDetailScreen";
import { ChannelsScreen } from "./ChannelsScreen";
import { IncidentScreen } from "./IncidentScreen";
import { EditRuleScreen, NewRuleScreen, parseNewRuleSearch } from "./RuleEditor";

/**
 * The alert screens mounted as the app mounts them, inside a `shell` layout route,
 * with a real query client and `fetch` answered from a table. Only the network is
 * replaced — the same arrangement as the synthetics harness.
 */

export const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0021";
const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;

export const minutesAgo = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

export const OWNERS: ChannelSummary = {
  id: "ch-owners",
  kind: "owners",
  name: "Organization owners",
  config: {},
  destination: null,
  secret_set: false,
  health: "healthy",
  failing_since: null,
  last_delivered_at: null,
  muted_until: null,
  muted_by: null,
  quiet_hours: null,
  created_at: minutesAgo(600),
  rules: [{ id: "rule-default", name: "Any check down", project_id: PROJECT_ID }],
};

export function channel(overrides: Partial<ChannelSummary> = {}): ChannelSummary {
  return {
    ...OWNERS,
    id: "ch-slack",
    kind: "slack",
    name: "#incidents",
    destination: "hooks.slack.com",
    secret_set: true,
    rules: [],
    ...overrides,
  };
}

export function rule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: "rule-default",
    project_id: PROJECT_ID,
    name: "Any check down",
    condition_kind: "check_state",
    condition: { kind: "check_state", check_ids: null, states: { down: "critical" } },
    channels: [{ id: "ch-owners", kind: "owners", name: "Organization owners" }],
    renotify_minutes: null,
    enabled: true,
    muted_until: null,
    muted_by: null,
    is_default: true,
    notifying_nobody: false,
    covers_nothing: false,
    created_at: minutesAgo(600),
    updated_at: minutesAgo(600),
    ...overrides,
  };
}

export function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: "inc-1",
    project_id: PROJECT_ID,
    rule_id: "rule-default",
    rule_name: "Any check down",
    condition_kind: "check_state",
    subject_key: "check:chk-1",
    subject_label: "Orders API",
    severity: "critical",
    opened_at: minutesAgo(7),
    resolved_at: null,
    is_event: false,
    flapping: false,
    muted: false,
    check_deleted: false,
    detail: {
      check_id: "chk-1",
      state: "down",
      reason: "the check answered 503 where under 400 was expected",
      location: "local",
    },
    notifications: [
      {
        id: "n-opened",
        channel_id: "ch-owners",
        channel_name: "Organization owners",
        channel_kind: "owners",
        kind: "opened",
        state: "delivered",
        reason: null,
        delivered_at: minutesAgo(7),
      },
    ],
    ...overrides,
  };
}

export interface ApiCall {
  method: string;
  path: string;
  search: URLSearchParams;
  body: unknown;
}
export type Handler = (call: ApiCall) => Response | Promise<Response>;
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

/** Every read the screens make, answered from fixtures. */
export function alertReads({
  rules = [rule()],
  incidents = { open: [], recent: [] },
  channels = [OWNERS],
  checks = [{ id: "chk-1", name: "Orders API" }],
}: {
  rules?: AlertRule[];
  incidents?: { open: Incident[]; recent: Incident[] };
  channels?: ChannelSummary[];
  checks?: { id: string; name: string }[];
} = {}): Handlers {
  const handlers: Handlers = {
    "GET /alerts/rules": json({ rules }),
    "GET /alerts/incidents": json(incidents),
    "GET /channels": json({ channels }),
    "GET /checks": json({ checks, available_locations: ["local"] }),
    "GET /services": json({
      services: [{ service_name: "checkout", request_count: 3400, error_rate: 0.012, p95_ms: 245 }],
    }),
    "GET /alerts/evaluation": json({
      sweeps: ["thresholds", "silence", "new_errors"].map((name) => ({
        name,
        finished_at: new Date().toISOString(),
        skipped: null,
        behind: false,
      })),
    }),
  };
  for (const each of rules) handlers[`GET /alerts/rules/${each.id}`] = json(each);
  for (const each of channels) {
    handlers[`GET /channels/${each.id}`] = json(each);
    handlers[`GET /channels/${each.id}/deliveries`] = json({ attempts: [] });
  }
  return handlers;
}

export function stubApi(handlers: Handlers) {
  const calls: ApiCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.startsWith(API_PREFIX)
        ? url.pathname.slice(API_PREFIX.length)
        : url.pathname;
      const call = {
        method,
        path,
        search: url.searchParams,
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
      calls.filter((call) => call.method === method && call.path === path),
  };
}

const mounted: Root[] = [];

export function unmountAll() {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
}

export async function mountAt(url: string) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setSelectedProjectId(PROJECT_ID);

  // The app's stale time (app/query-client.ts), so a screen that forgets to
  // invalidate after a change shows stale data here as it would in the app.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: Outlet });
  const shellRoute = createRoute({ getParentRoute: () => rootRoute, id: "shell" });
  const placeholder = (text: string) => () => createElement("p", null, text);
  const routeTree = rootRoute.addChildren([
    shellRoute.addChildren([
      createRoute({ getParentRoute: () => shellRoute, path: "/alerts", component: AlertsScreen }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/alerts/rules/new",
        component: NewRuleScreen,
        validateSearch: parseNewRuleSearch,
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/alerts/rules/$ruleId",
        component: EditRuleScreen,
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/alerts/incidents/$incidentId",
        component: IncidentScreen,
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/settings/channels",
        component: ChannelsScreen,
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/settings/channels/$channelId",
        component: ChannelDetailScreen,
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/synthetics/$checkId",
        component: placeholder("Check"),
      }),
      createRoute({
        getParentRoute: () => shellRoute,
        path: "/services/$name",
        component: placeholder("Service"),
      }),
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
  return { container, router };
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

export function button(container: Element, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!found)
    throw new Error(`no button labelled "${label}"; the page reads: ${readable(container)}`);
  return found;
}

export async function click(element: Element) {
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
}

export async function typeInto(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype =
    input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(input, value);
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

export function field(container: Element, name: string): HTMLInputElement {
  const found = container.querySelector<HTMLInputElement>(`input[name="${name}"]`);
  if (!found) throw new Error(`no input named ${name}`);
  return found;
}

export async function submit(form: HTMLFormElement) {
  await act(async () => form.requestSubmit());
  await settle();
}
