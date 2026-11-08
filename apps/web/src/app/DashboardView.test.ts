// @vitest-environment jsdom

import { QueryClientProvider } from "@tanstack/react-query";
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

import type {
  Dashboard,
  DashboardContext,
  DashboardSummary,
  PanelReadResponse,
  PanelResult,
  StoredPanel,
} from "@/features/dashboards/api";
import { PANEL_READ_CLIENT_TIMEOUT_MS } from "@/features/dashboards/api";
import { DashboardListScreen } from "@/features/dashboards/DashboardListScreen";
import { DashboardScreen } from "@/features/dashboards/DashboardScreen";
import { apiClient } from "@/shared/api-client";
import type { DetectionStatus } from "@/shared/detection/headline";
import { formatRelativeTime } from "@/shared/format/time";
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId, setSelectedRangeId } from "@/shared/selection";
import { dismissToast, getToasts } from "@/shared/toast/toast-store";

// The API's own budget, read as text: this package compiles without node types.
import policy from "../../../api/src/nexovue_api/ratelimit/policy.py?raw";
import { createQueryClient } from "./query-client";

/**
 * The dashboard read view and list (F20, T2.18), with a real router and the console's real
 * query client: at the app layer, because a feature may not import `app/` (`biome.json`), so a 503's retry and a 429's toast are the app's own behaviour, not a
 * stub's. Only `fetch` is a table.
 *
 * Each panel state, and BR5 (a failed panel with no earlier reading shows words and no
 * chart; one with an earlier reading keeps it under the strip for one interval, then words
 * alone: AC20, for `failed` and `refused`), AC61 (a cold ClickHouse's 503: one batch a
 * refresh, no retry, no toast, notes still drawn, one line), AC97 (detection's line),
 * AC100 (the "could not be read" count, focusing the first), AC111 (each panel a section
 * named by its heading; correlation ids), AC145 (a whole-request 429 says FR45's sentence,
 * with no toast, while other screens keep theirs), FR61 (escapes), AC7 (a viewer's list).
 */

const API_PREFIX = "/api/v1";
const PROJECT_ID = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const DASHBOARD_ID = "0190e3f4-0000-7000-8000-000000000001";
// The real clock, to the minute: relative words ("3 hours ago") read it.
const NOW = Math.floor(Date.now() / 60_000) * 60_000;
const SETTLE_ROUNDS = 60;
const MINUTE_MS = 60_000;

const at = (minutesAgo: number) => new Date(NOW - minutesAgo * MINUTE_MS).toISOString();

const panel = (id: string, kind: string, settings: Record<string, unknown>): StoredPanel => ({
  id,
  kind,
  title: null,
  width: 4,
  height: "medium",
  settings,
});

const RATE = panel("p_rate", "project_trend", { signal: "rate" });
const CHECKOUT = panel("p_checkout", "service_trend", { service: "checkout", signal: "latency" });
const NOTE = {
  ...panel("p_note", "note", { text: "Check the queue <b>first</b>" }),
  height: "small" as const,
};

function dashboard(panels: StoredPanel[] = [RATE, CHECKOUT, NOTE]): Dashboard {
  return {
    id: DASHBOARD_ID,
    title: "Checkout on-call",
    description: "What the checkout team watches.",
    definition: {
      schema_version: 1,
      refresh_seconds: 60,
      default_range: null,
      service_picker: false,
      links: [{ label: "Runbook", url: "https://wiki.example/checkout" }],
      panels,
    },
    version: 1,
    owner: "Asha",
    updated_by: "Asha",
    created_at: at(600),
    updated_at: at(600),
  };
}

const judging: DetectionStatus = {
  state: "judging",
  services_total: 2,
  services_learning: 0,
  services_judged: 2,
  services_delayed: 0,
  services_muted: 0,
  earliest_judging_date: null,
  not_running_since: null,
  delayed_since: null,
  open_findings: 1,
  services_too_many: 0,
  judged_service_limit: 50,
};

function context(detection: DetectionStatus = judging): DashboardContext {
  return {
    project: { id: PROJECT_ID, name: "production", timezone: "UTC", retention: null },
    window_start: at(60),
    window_end: at(0),
    detection,
    ever_reported: true,
    last_seen_at: at(1),
    findings: [],
    findings_truncated: false,
    open_findings: { total: 1, by_service: { checkout: 1 } },
  };
}

const ok = (label = "requests_per_minute", kind = "project_trend"): PanelResult => ({
  state: "ok",
  kind,
  read_at: at(0),
  bucket_seconds: 60,
  data_starts_at: at(3),
  data_ends_at: at(1),
  series: [
    {
      label,
      points: [
        [at(3), 10],
        [at(2), 12],
        [at(1), 11],
      ],
    },
  ],
});

function batch(panels: Record<string, PanelResult>): PanelReadResponse {
  return { correlation_id: "corr-42", window_start: at(60), window_end: at(0), panels };
}

type Handler = () => Response;
const json =
  (body: unknown, status = 200, headers: Record<string, string> = {}): Handler =>
  () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", ...headers },
    });
const problem = (status: number, title: string, headers: Record<string, string> = {}): Handler =>
  json({ type: "about:blank", title, status }, status, {
    "content-type": "application/problem+json",
    ...headers,
  });

function stubApi(handlers: Record<string, Handler | Handler[]>, role: TestRole = "owner") {
  const calls: Array<{ method: string; path: string }> = [];
  const queues: Record<string, Handler[]> = {};
  const all: Record<string, Handler | Handler[]> = {
    "GET /auth/me": json(identityFor(role)),
    ...handlers,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path });
      const key = `${method} ${path}`;
      const handler = all[key];
      if (!handler) throw new Error(`no stub for ${key}`);
      if (Array.isArray(handler)) {
        queues[key] ??= [...handler];
        const next = queues[key].length > 1 ? queues[key].shift() : queues[key][0];
        if (!next) throw new Error(`no answer left for ${key}`);
        return next();
      }
      return handler();
    }),
  );
  return calls;
}

function viewOf(answer: Handler | Handler[], extra: Record<string, Handler> = {}) {
  return {
    [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard()),
    [`GET /dashboards/${DASHBOARD_ID}/context`]: json(context()),
    "GET /dashboard-panels/read": answer,
    ...extra,
  };
}

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  for (const toast of getToasts()) dismissToast(toast.id);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function mountAt(initial: string) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setSelectedProjectId(PROJECT_ID);
  setSelectedRangeId("1h");
  const client = createQueryClient();
  const rootRoute = createRootRoute({ component: Outlet });
  const list = createRoute({
    getParentRoute: () => rootRoute,
    path: "/dashboards",
    component: DashboardListScreen,
  });
  const view = createRoute({
    getParentRoute: () => rootRoute,
    path: "/dashboards/$dashboardId",
    component: () => createElement(DashboardScreen, { dashboardId: DASHBOARD_ID }),
  });
  const service = createRoute({
    getParentRoute: () => rootRoute,
    path: "/services/$name",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([list, view, service]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => {
    root.render(
      createElement(QueryClientProvider, { client }, createElement(RouterProvider, { router })),
    );
  });
  return { container, client };
}

async function waitFor(check: () => unknown, what: string): Promise<void> {
  for (let round = 0; round < SETTLE_ROUNDS; round += 1) {
    if (check()) return;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  }
  throw new Error(`never saw ${what}`);
}

const text = (node: Element | null) => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
const section = (container: Element, id: string) => container.querySelector(`#panel-${id}`);
const drawn = (node: Element | null) =>
  node?.querySelector('[role="img"]') !== null && node?.querySelector('[role="img"]') !== undefined;

describe("the dashboard view", () => {
  it("draws each panel as a section named by its heading, and a note as plain text (AC111, AC36)", async () => {
    stubApi(viewOf(json(batch({ p_rate: ok(), p_checkout: ok("p95_ms", "service_trend") }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => drawn(section(container, "p_rate")), "the rate chart");

    for (const id of ["p_rate", "p_checkout", "p_note"]) {
      const node = section(container, id);
      const heading = node?.querySelector("h2");
      expect(node?.tagName).toBe("SECTION");
      expect(node?.getAttribute("aria-labelledby")).toBe(heading?.id);
    }
    expect(text(section(container, "p_rate")?.querySelector("h2") ?? null)).toBe("Rate");
    expect(text(section(container, "p_checkout")?.querySelector("h2") ?? null)).toBe(
      "checkout · Latency",
    );
    expect(text(section(container, "p_note"))).toContain("Check the queue <b>first</b>");
    expect(section(container, "p_note")?.querySelector("b")).toBeNull();
    expect(text(container)).toContain("production · times in");
    const link = container.querySelector('a[href="https://wiki.example/checkout"]');
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(text(link?.parentElement ?? null)).toContain("wiki.example");
  });

  it("passes the batch's 25 s budget to the client, as the API's deadline and margin add up (D25)", async () => {
    const seconds = (name: string) =>
      Number(new RegExp(`${name}: Final = (\\d+)`).exec(policy)?.[1]);
    expect(PANEL_READ_CLIENT_TIMEOUT_MS).toBe(
      (seconds("PANEL_READ_DEADLINE_SECONDS") + seconds("PANEL_READ_MARGIN_SECONDS")) * 1000,
    );

    const get = vi.spyOn(apiClient, "get");
    stubApi(viewOf(json(batch({ p_rate: ok() }))));
    await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => get.mock.calls.some(([path]) => path === "dashboard-panels/read"),
      "the batch",
    );
    const [, options] = get.mock.calls.find(([path]) => path === "dashboard-panels/read") ?? [];
    expect(options?.timeoutMs).toBe(PANEL_READ_CLIENT_TIMEOUT_MS);
    expect(options?.searchParams).toMatchObject({ range: "1h", project_id: PROJECT_ID });
  });

  it.each(["failed", "refused", "timed_out"] as const)(
    "a %s panel with no earlier reading shows words, the reference, and no chart (BR5, AC20)",
    async (state) => {
      const failing: PanelResult =
        state === "refused"
          ? { state, kind: "service_trend", retry_after_seconds: 20 }
          : { state, kind: "service_trend" };
      stubApi(viewOf(json(batch({ p_rate: ok(), p_checkout: failing }))));
      const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
      await waitFor(() => drawn(section(container, "p_rate")), "the healthy panel");

      const node = section(container, "p_checkout");
      expect(drawn(node)).toBe(false);
      expect(text(node)).toContain(
        {
          failed: "Could not read this.",
          refused: "Too many reads just now.",
          timed_out: "The database took too long to answer.",
        }[state],
      );
      expect(text(node)).toContain("corr-42");
    },
  );

  it.each(["failed", "refused"] as const)(
    "a %s panel keeps its earlier line under the strip for one interval, then words alone (AC20)",
    async (state) => {
      const failing: PanelResult =
        state === "refused"
          ? { state, kind: "project_trend", retry_after_seconds: 20 }
          : { state, kind: "project_trend" };
      stubApi(viewOf([json(batch({ p_rate: ok() })), json(batch({ p_rate: failing }))]));
      const clock = vi.spyOn(Date, "now").mockReturnValue(NOW);
      const { container, client } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
      await waitFor(() => drawn(section(container, "p_rate")), "the first reading");

      // A minute's refresh later, the read fails.
      clock.mockReturnValue(NOW + MINUTE_MS);
      await act(async () => {
        await client.refetchQueries({ queryKey: ["dashboard-panels"] });
      });
      await waitFor(
        () => text(section(container, "p_rate")).includes("Line below is old"),
        "the strip",
      );
      expect(drawn(section(container, "p_rate"))).toBe(true);

      // One interval after it first failed, it fails again: the old line goes.
      clock.mockReturnValue(NOW + 2 * MINUTE_MS + 1_000);
      await act(async () => {
        await client.refetchQueries({ queryKey: ["dashboard-panels"] });
      });
      await waitFor(() => !drawn(section(container, "p_rate")), "the old line to go");
      expect(text(section(container, "p_rate"))).not.toContain("Line below is old");
    },
  );

  it("says each other state in its own words, with telemetry values escaped (FR16, FR8, FR61)", async () => {
    const stranger = panel("p_stranger", "service_trend", {
      service: "check\u202Eout",
      signal: "rate",
    });
    const quiet = panel("p_quiet", "service_trend", { service: "quiet", signal: "rate" });
    const gone = panel("p_gone", "retired_kind", {});
    stubApi({
      [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard([RATE, stranger, quiet, gone])),
      [`GET /dashboards/${DASHBOARD_ID}/context`]: json(context()),
      "GET /dashboard-panels/read": json(
        batch({
          p_rate: { state: "empty", kind: "project_trend", read_at: at(0), last_seen_at: null },
          p_stranger: { state: "not_in_project", kind: "service_trend" },
          p_quiet: { state: "empty", kind: "service_trend", read_at: at(0), last_seen_at: at(180) },
          p_gone: { state: "unknown_kind", kind: "unknown_kind" },
        }),
      ),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(section(container, "p_gone")).includes("no longer available"),
      "the words",
    );

    expect(text(section(container, "p_rate"))).toContain("Nothing reported in this window.");
    expect(text(section(container, "p_stranger"))).toContain(
      "No record of check[U+202E]out in production.",
    );
    expect(text(section(container, "p_stranger")?.querySelector("h2") ?? null)).toBe(
      "check[U+202E]out · Rate",
    );
    expect(text(section(container, "p_quiet"))).toContain(
      `No requests in this window. Last seen ${formatRelativeTime(at(180))}.`,
    );
    expect(text(section(container, "p_gone"))).toContain("This panel type is no longer available.");
  });

  it.each([
    ["owner", "Remove 1 panel."],
    ["viewer", "Ask Asha, or someone who can edit it, to remove panels."],
  ] as const)(
    "words past the scan cap follow the reader's role: %s (FR65)",
    async (role, words) => {
      stubApi(
        viewOf(
          json(batch({ p_rate: ok(), p_checkout: { state: "over_limit", kind: "service_trend" } })),
        ),
        role,
      );
      const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
      await waitFor(
        () => text(section(container, "p_checkout")).includes("more reads"),
        "the words",
      );

      expect(text(section(container, "p_checkout"))).toContain(words);
    },
  );

  it("says how many panels could not be read, and its link moves focus to the first (AC100)", async () => {
    stubApi(
      viewOf(json(batch({ p_rate: ok(), p_checkout: { state: "failed", kind: "service_trend" } }))),
    );
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => text(container).includes("could not be read"), "the count");

    const count = [...container.querySelectorAll("button")].find((button) =>
      text(button).includes("could not be read"),
    );
    expect(text(count ?? null)).toBe("1 of 3 panels could not be read");
    act(() => count?.click());
    expect(document.activeElement?.id).toBe("panel-p_checkout");
  });

  it("hides the count when every panel read", async () => {
    stubApi(viewOf(json(batch({ p_rate: ok(), p_checkout: ok("p95_ms", "service_trend") }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => drawn(section(container, "p_checkout")), "the panels");

    expect(text(container)).not.toContain("could not be read");
  });

  it("with a cold ClickHouse's 503: one batch, no retry, no toast, notes drawn, one line (AC61)", async () => {
    const calls = stubApi(viewOf(problem(503, "A dependency this request needs is not available")));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => text(container).includes("Could not read telemetry"), "the line");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300));
    });

    expect(calls.filter((call) => call.path === "/dashboard-panels/read")).toHaveLength(1);
    expect(getToasts()).toEqual([]);
    expect(text(container)).toContain("Notes and findings below are current.");
    expect(text(section(container, "p_note"))).toContain("Check the queue");
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
  });

  it("a whole-request 429 says FR45's sentence with its reference and raises no toast (AC145)", async () => {
    stubApi(
      viewOf(
        problem(429, "Too many requests", { "retry-after": "20", "x-correlation-id": "corr-429" }),
      ),
    );
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => text(container).includes("Too many reads just now."), "the sentence");

    expect(getToasts()).toEqual([]);
    expect(text(container.querySelector('[role="alert"]'))).toMatch(/Reference \S+/);
  });

  it("names detection's state above the grid when it has something to say (AC97)", async () => {
    stubApi({
      ...viewOf(json(batch({ p_rate: ok() }))),
      [`GET /dashboards/${DASHBOARD_ID}/context`]: json(
        context({ ...judging, state: "not_running", open_findings: 0, not_running_since: null }),
      ),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);

    await waitFor(() => text(container).includes("Detection is not running"), "detection's line");
  });

  it("says a dashboard that does not exist plainly, with a way back to the list (AC75)", async () => {
    stubApi({
      [`GET /dashboards/${DASHBOARD_ID}`]: problem(404, "No such dashboard in this organization"),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);

    await waitFor(
      () => text(container).includes("does not exist or is no longer available"),
      "the page state",
    );
    expect(container.querySelector('a[href="/dashboards"]')).not.toBeNull();
  });
});

describe("the dashboard list", () => {
  const summary: DashboardSummary = {
    id: DASHBOARD_ID,
    title: "Checkout on-call",
    description: "",
    owner: null,
    panel_count: 3,
    uses_picker: false,
    panels_not_reading: 0,
    last_viewed_at: null,
    created_at: at(60),
    updated_at: at(60),
  };
  const templates = {
    templates: [
      {
        id: "start_from_overview",
        name: "Start from Overview",
        document: { title: "Overview", description: "", definition: dashboard([RATE]).definition },
      },
    ],
  };

  it("offers a viewer nothing that would fail on an empty list (AC7)", async () => {
    stubApi(
      { "GET /dashboards": json({ dashboards: [] }), "GET /dashboards/templates": json(templates) },
      "viewer",
    );
    const { container } = await mountAt("/dashboards");
    await waitFor(
      () => text(container).includes("Ask a member to create one."),
      "the viewer's empty state",
    );

    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("lets an editor start from Overview, name it and save it, and opens the saved dashboard", async () => {
    const calls = stubApi({
      "GET /dashboards": json({ dashboards: [] }),
      "GET /dashboards/templates": json(templates),
      "POST /dashboards": json(dashboard([RATE]), 201),
      ...viewOf(json(batch({ p_rate: ok() }))),
    });
    const { container } = await mountAt("/dashboards");
    await waitFor(() => text(container).includes("Start from Overview"), "the action");

    act(() =>
      [...container.querySelectorAll("button")]
        .find((b) => text(b) === "Start from Overview")
        ?.click(),
    );
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    const save = [...container.querySelectorAll("button")].find((b) => text(b) === "Save");
    await act(async () => save?.click());

    await waitFor(
      () => calls.some((call) => call.path === "/dashboard-panels/read"),
      "the saved dashboard",
    );
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("names a former member as such, and a never-opened dashboard as never opened (FR38)", async () => {
    stubApi({
      "GET /dashboards": json({ dashboards: [summary] }),
      "GET /dashboards/templates": json(templates),
    });
    const { container } = await mountAt("/dashboards");
    await waitFor(() => text(container).includes("Checkout on-call"), "the row");

    expect(text(container.querySelector("tbody"))).toContain("Former member");
    expect(text(container.querySelector("tbody"))).toContain("Never");
  });

  it("keeps the console's toast for a 429 on any other screen (AC145)", async () => {
    stubApi({
      "GET /dashboards": problem(429, "Too many requests"),
      "GET /dashboards/templates": json(templates),
    });
    await mountAt("/dashboards");

    await waitFor(
      () => getToasts().some((toast) => toast.message === "Rate limited by the API."),
      "the toast",
    );
  });
});
