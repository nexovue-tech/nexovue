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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
import { readSelection, setSelectedProjectId, setSelectedRangeId } from "@/shared/selection";
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
    last_viewed_at: null,
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
    markers: [],
    markers_truncated: false,
    markers_cap: 500,
    coverage: {},
    incidents: [],
    incidents_truncated: false,
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

const typedProblem = (status: number, slug: string): Handler =>
  json({ type: `https://nexovue.com/problems/${slug}`, title: slug, status }, status, {
    "content-type": "application/problem+json",
  });

function stubApi(handlers: Record<string, Handler | Handler[]>, role: TestRole = "owner") {
  const calls: Array<{ method: string; path: string; search: string }> = [];
  const queues: Record<string, Handler[]> = {};
  const all: Record<string, Handler | Handler[]> = {
    "GET /auth/me": json(identityFor(role)),
    "GET /projects": json({
      projects: [
        { id: PROJECT_ID, slug: "production", name: "production", first_reported_on: at(9000) },
      ],
    }),
    "GET /system/info": json({
      service: "api",
      revision: "test",
      build_time: at(0),
      deployment: "self-hosted",
    }),
    ...handlers,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      calls.push({ method, path, search: url.search });
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
      // A refused panel reads again by itself first, and quotes its reference only once
      // the tries are spent (FR45, AC111): see the catch-up cases below.
      if (state === "refused") {
        expect(text(node)).toContain("Reading again in 20 s");
        expect(text(node)).not.toContain("corr-42");
      } else {
        expect(text(node)).toContain("corr-42");
      }
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

  it("names Step 4's charts and says their empty states in their own words (T4.1, T4.2)", async () => {
    const top = panel("p_top", "top_services_trend", {
      signal: "errors",
      order: "worst",
      count: 5,
    });
    const node = panel("p_node", "node_trend", {
      node: { host: "web\u202E01", container: "", reporter: "", reporter_instance: "" },
      metric: "system.cpu.utilization",
    });
    stubApi({
      [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard([top, node])),
      [`GET /dashboards/${DASHBOARD_ID}/context`]: json(context()),
      "GET /dashboard-panels/read": json(
        batch({
          p_top: { state: "empty", kind: "top_services_trend", read_at: at(0), last_seen_at: null },
          p_node: { state: "empty", kind: "node_trend", read_at: at(0), last_seen_at: null },
        }),
      ),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => text(section(container, "p_node")).includes("reported"), "the words");

    expect(text(section(container, "p_top")?.querySelector("h2") ?? null)).toBe(
      "Worst 5 services · Errors",
    );
    expect(text(section(container, "p_top"))).toContain("No services reported in this window.");
    expect(text(section(container, "p_node")?.querySelector("h2") ?? null)).toBe(
      "web[U+202E]01 · system.cpu.utilization",
    );
    expect(text(section(container, "p_node"))).toContain(
      "web[U+202E]01 has not reported in this window.",
    );
  });

  it("offers every chart's numbers as a table, opened at the full row's width (FR34, FR49)", async () => {
    stubApi(viewOf(json(batch({ p_rate: ok(), p_checkout: ok("p95_ms", "service_trend") }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => section(container, "p_checkout")?.querySelector("canvas, [role=img]"),
      "a chart",
    );
    const checkout = () => section(container, "p_checkout");
    const toggle = [...(checkout()?.querySelectorAll("button") ?? [])].find(
      (each) => text(each) === "View as table",
    );
    expect(toggle).toBeDefined();
    expect(checkout()?.className).toMatch(/@3xl:col-span/);

    act(() => toggle?.click());

    expect(checkout()?.querySelector("table")).not.toBeNull();
    expect(text(checkout()?.querySelector("thead") ?? null)).toContain("p95 (est.)");
    expect(checkout()?.className).not.toMatch(/@3xl:col-span/);
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

  it("says once, in the page's one live region, how many panels were read (AC143)", async () => {
    stubApi(
      viewOf(json(batch({ p_rate: ok(), p_checkout: { state: "failed", kind: "service_trend" } }))),
    );
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => drawn(section(container, "p_rate")), "the healthy panel");

    const regions = container.querySelectorAll('[aria-live], [role="status"], [role="log"]');
    expect(regions).toHaveLength(1);
    expect(text(regions[0])).toBe("Dashboard loaded, 1 of 2 panels read");
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

  it("draws Step 5's number, table and open findings, and an outage leaves the findings (T5.6a, AC61)", async () => {
    const number = {
      ...panel("p_number", "project_number", { measure: "p95_ms", compare: true }),
      height: "small" as const,
    };
    const table = panel("p_table", "services_table", { order: "worst", rows: 1 });
    const open = {
      ...panel("p_open", "open_findings", { service: null }),
      height: "small" as const,
    };
    const hostile = "<img src=x onerror=alert(1)>";
    stubApi({
      ...viewOf(
        json(
          batch({
            p_number: {
              ...(ok() as Extract<PanelResult, { state: "ok" }>),
              kind: "project_number",
              series: [],
              number: { measure: "p95_ms", value: 88, previous: null, previous_coverage: 0.5 },
            },
            p_table: {
              ...(ok() as Extract<PanelResult, { state: "ok" }>),
              kind: "services_table",
              series: [],
              rows: [{ service_name: hostile, requests_per_minute: 4, error_rate: 0, p95_ms: 9 }],
            },
          }),
        ),
      ),
      [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard([number, table, open])),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => text(section(container, "p_table")).includes(hostile), "the table");

    expect(text(section(container, "p_number"))).toContain("88ms");
    expect(text(section(container, "p_number"))).toContain("Not enough history to compare");
    // A hostile value is text, never an element (AC35's render half).
    expect(section(container, "p_table")?.querySelector("img")).toBeNull();
    expect(text(section(container, "p_table")?.querySelector("thead") ?? null)).toContain(
      "p95 (est.)",
    );
    // Open findings come from the context, with no scan, judged by detection (AC97).
    expect(text(section(container, "p_open"))).toContain("1");
  });

  it("draws a metric series and says what it combined, two units and a changed series (Step 8)", async () => {
    const metric = (id: string, series: string | null) =>
      panel(id, "metric_series", {
        metric: "orders_processed",
        shape: "sum",
        service: "checkout",
        series,
        aggregation: "sum",
      });
    const reading = ok() as Extract<PanelResult, { state: "ok" }>;
    stubApi({
      ...viewOf(
        json(
          batch({
            p_all: {
              ...reading,
              kind: "metric_series",
              series: reading.series.slice(0, 1).map((line) => ({ ...line, attributes: null })),
              series_combined: 3,
              units: ["1"],
            },
            p_units: {
              ...reading,
              kind: "metric_series",
              series: [],
              series_combined: 2,
              units: ["ms", "s"],
            },
            p_changed: { state: "series_changed", kind: "metric_series" },
          }),
        ),
      ),
      [`GET /dashboards/${DASHBOARD_ID}`]: json(
        dashboard([metric("p_all", null), metric("p_units", null), metric("p_changed", "42")]),
      ),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(section(container, "p_all")).includes("Combines 3 series."),
      "the combined line",
    );

    expect(text(section(container, "p_all"))).toContain("orders_processed · all series, total");
    expect(text(section(container, "p_all"))).toContain(
      "All findings and releases in this project",
    );
    expect(text(section(container, "p_units"))).toContain(
      "orders_processed arrives in 2 units in this window (ms, s), so its series are not added together.",
    );
    expect(text(section(container, "p_changed"))).toContain(
      "orders_processed still reports, under different attributes. Choose again.",
    );
  });

  it("offers a raw panel past its window the widest one it may read (FR17, AC23)", async () => {
    const check = panel("p_check", "check_trend", { check: "Login", signal: "uptime" });
    stubApi({
      ...viewOf(
        json(batch({ p_check: { state: "window_too_wide", kind: "check_trend", limit_days: 30 } })),
      ),
      [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard([check])),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(section(container, "p_check")).includes("at most 30 days"),
      "the words",
    );

    expect(text(section(container, "p_check"))).toContain("Show the last 7 days");
  });

  it("lists the window's findings, incidents and releases above the grid (FR47)", async () => {
    stubApi({
      ...viewOf(json(batch({ p_rate: ok() }))),
      [`GET /dashboards/${DASHBOARD_ID}/context`]: json({
        ...context(),
        findings: [
          {
            id: "f-1",
            scope: "service",
            service_name: "checkout",
            severity: "critical",
            title: "checkout is slower than normal",
            started_at: at(30),
            closed_at: null,
          },
        ],
      }),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => text(container).includes("In this window"), "the strip");

    expect(container.querySelector('a[href="/findings/f-1"]')?.textContent).toBe(
      "checkout is slower than normal",
    );
    expect(text(container)).toContain("Findings are judged from their own readings");
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
    deleted_at: null,
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

    // Recently deleted is a read; there is nothing to create, copy or restore (AC7).
    expect([...container.querySelectorAll("button")].map((each) => text(each))).toEqual([
      "Recently deleted (0)",
    ]);
  });

  it("offers an editor the starting point and a blank one, each opening the builder as a draft (FR39)", async () => {
    const calls = stubApi({
      "GET /dashboards": json({ dashboards: [] }),
      "GET /dashboards/templates": json(templates),
    });
    const { container } = await mountAt("/dashboards");
    await waitFor(() => text(container).includes("Start from Overview"), "the action");

    const links = [...container.querySelectorAll("a")].map((link) => [
      text(link),
      link.getAttribute("href"),
    ]);
    expect(links).toEqual([
      ["Start from Overview", "/dashboards/new?template=start_from_overview"],
      ["New dashboard", "/dashboards/new"],
    ]);
    // Opening a starting point writes nothing (AC38); the builder's test saves one.
    expect(calls.some((call) => call.method === "POST")).toBe(false);
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

const OTHER_PROJECT = "7b2e1d3f-4c5a-4b6e-8f9a-8d7c6b5a4e3f";
const FIXED_NOW = new Date(NOW);
const searchOf = (call: { search: string }) => new URLSearchParams(call.search);

describe("the dashboard at an address (F20, Step 6)", () => {
  beforeEach(() => {
    // Pinned: every instant here is relative to `NOW`, and the page reads the date for its lines.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(FIXED_NOW);
  });

  const twoProjects = {
    "GET /projects": json({
      projects: [
        { id: PROJECT_ID, slug: "production", name: "production", first_reported_on: at(9000) },
        { id: OTHER_PROJECT, slug: "staging", name: "staging", first_reported_on: at(9000) },
      ],
    }),
  };

  it("reads project B for a link naming it, and no read reaches project A first (AC27)", async () => {
    const calls = stubApi({
      ...twoProjects,
      ...viewOf(json(batch({ p_rate: ok() }))),
      [`GET /dashboards/${DASHBOARD_ID}/context`]: json({
        ...context(),
        project: { id: OTHER_PROJECT, name: "staging", timezone: "UTC", retention: null },
      }),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?project=${OTHER_PROJECT}`);
    await waitFor(() => drawn(section(container, "p_rate")), "the chart");

    const reads = calls.filter((call) => /dashboard-panels|context/.test(call.path));
    expect(reads.length).toBeGreaterThan(1);
    for (const call of reads) expect(searchOf(call).get("project_id")).toBe(OTHER_PROJECT);
    expect(text(container)).toContain("Showing staging from this link. You were on production.");
    expect(text(container)).toContain("Back to production");
    expect(readSelection().projectId).toBe(OTHER_PROJECT);
  });

  it("refuses a link to a project the reader cannot open, with no read at all (AC69)", async () => {
    const calls = stubApi({ ...viewOf(json(batch({ p_rate: ok() }))) });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?project=${OTHER_PROJECT}`);
    await waitFor(() => text(container).includes("a project you cannot open"), "the refusal");

    expect(calls.filter((call) => /dashboard-panels|context/.test(call.path))).toEqual([]);
    expect(readSelection().projectId).toBe(PROJECT_ID);
    expect(text(container)).toContain("View against mine");
    expect(drawn(section(container, "p_rate"))).toBe(false);
  });

  it("tells the two 404s apart by the problem's type, as an unknown id reads as a foreign one (AC75)", async () => {
    stubApi({
      ...viewOf(json(batch({}))),
      [`GET /dashboards/${DASHBOARD_ID}/context`]: typedProblem(404, "project-not-found"),
    });
    const project = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(project.container).includes("a project you cannot open"),
      "project 404",
    );

    stubApi({
      ...viewOf(json(batch({}))),
      [`GET /dashboards/${DASHBOARD_ID}/context`]: typedProblem(404, "dashboard-not-found"),
    });
    const deleted = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(deleted.container).includes("This dashboard was deleted."),
      "dashboard 404",
    );
    expect(text(deleted.container)).not.toContain("a project you cannot open");
  });

  it("shows a link's unknown service quoted and marked, never as the reader's own (AC123)", async () => {
    const picker = {
      ...panel("p_pick", "service_trend", { service: { picker: true }, signal: "rate" }),
    };
    stubApi({
      ...viewOf(json(batch({ p_pick: { state: "not_in_project", kind: "service_trend" } }))),
      [`GET /dashboards/${DASHBOARD_ID}`]: json({
        ...dashboard([picker]),
        definition: { ...dashboard([picker]).definition, service_picker: true },
      }),
    });
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?service=${"y".repeat(60)}`);
    await waitFor(() => text(container).includes("from this link was not found"), "the line");
    expect(text(container)).toContain(
      `“${"y".repeat(40)}…” from this link was not found in production.`,
    );
  });

  it("passes the link's service to the read", async () => {
    const calls = stubApi(viewOf(json(batch({ p_rate: ok() }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?service=checkout`);
    await waitFor(() => drawn(section(container, "p_rate")), "the chart");
    const read = calls.find((call) => call.path === "/dashboard-panels/read");
    expect(searchOf(read ?? { search: "" }).get("service")).toBe("checkout");
  });

  it("says a fixed window by its times, and does not refresh it (AC28)", async () => {
    const from = at(180);
    const to = at(120);
    const calls = stubApi(viewOf(json(batch({ p_rate: ok() }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?from=${from}&to=${to}`);
    await waitFor(() => drawn(section(container, "p_rate")), "the chart");
    expect(text(container)).toContain("Fixed time:");
    expect(text(container)).toContain("Back to live");
    expect(text(container)).not.toContain("can still change");
    const read = calls.find((call) => call.path === "/dashboard-panels/read");
    expect(searchOf(read ?? { search: "" }).get("from")).toBe(from);
    expect(searchOf(read ?? { search: "" }).get("range")).toBeNull();
  });

  it("warns that a window ending in recent minutes can still change (AC28)", async () => {
    stubApi(viewOf(json(batch({ p_rate: ok() }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?from=${at(60)}&to=${at(2)}`);
    await waitFor(() => text(container).includes("Fixed time:"), "the line");
    expect(text(container)).toContain("Its newest minutes can still change.");
  });

  it("offers both links, exact times first on a fixed window, and says which was copied (AC137)", async () => {
    const written: string[] = [];
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: async (url: string) => void written.push(url) },
    });
    stubApi(viewOf(json(batch({ p_rate: ok() }))));
    const { container } = await mountAt(
      `/dashboards/${DASHBOARD_ID}?from=${at(180)}&to=${at(120)}`,
    );
    await waitFor(() => drawn(section(container, "p_rate")), "the chart");

    const copy = [...container.querySelectorAll("button")].find(
      (b) => b.textContent === "Copy link",
    );
    await act(async () => copy?.click());
    const options = [...container.querySelectorAll("li button")].map((b) => text(b));
    expect(options[0]).toMatch(/^Link to these exact times \(/);
    expect(options[1]).toBe("Link that stays live (last hour)");

    await act(async () => (container.querySelectorAll("li button")[1] as HTMLElement).click());
    const link = new URL(written[0] ?? "");
    expect(link.searchParams.get("project")).toBe(PROJECT_ID);
    expect(link.searchParams.get("range")).toBe("1h");
    expect(link.searchParams.get("from")).toBeNull();
    expect(text(container)).toContain("Copied the link that stays live.");
  });

  it("puts the live link first on a live window", async () => {
    stubApi(viewOf(json(batch({ p_rate: ok() }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => drawn(section(container, "p_rate")), "the chart");
    const copy = [...container.querySelectorAll("button")].find(
      (b) => b.textContent === "Copy link",
    );
    await act(async () => copy?.click());
    expect(text(container.querySelectorAll("li button")[0] ?? null)).toBe(
      "Link that stays live (last hour)",
    );
  });

  it("says who can read a note by install, and that text can be purged (AC119)", async () => {
    stubApi(viewOf(json(batch({ p_rate: ok() }))));
    const selfHosted = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(section(selfHosted.container, "p_note")).includes("Everyone"),
      "the note",
    );
    const own = text(section(selfHosted.container, "p_note"));
    expect(own).toContain(
      "Everyone in this organization can read this. Do not paste secrets or personal details.",
    );
    expect(own).not.toMatch(/support/i);
    expect(own).toContain("purge");

    stubApi({
      ...viewOf(json(batch({ p_rate: ok() }))),
      "GET /system/info": json({
        service: "api",
        revision: "t",
        build_time: at(0),
        deployment: "hosted",
      }),
    });
    const hosted = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(section(hosted.container, "p_note")).includes("support"),
      "the hosted note",
    );
    const theirs = text(section(hosted.container, "p_note"));
    expect(theirs).toContain("can read this, and support while an owner has granted access.");
    expect(theirs).toContain("purge");
  });

  it("offers export to a viewer, saying first what the file holds, and the file names nothing of this install (AC119, AC40)", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    stubApi(viewOf(json(batch({ p_rate: ok() }))), "viewer");
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    const exportButton = () =>
      [...container.querySelectorAll<HTMLButtonElement>("header button")].find(
        (each) => text(each) === "Export",
      );
    await waitFor(exportButton, "export");
    // Only once the page is drawn: the router builds addresses with the real `URL`.
    const saved: Blob[] = [];
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: (blob: Blob) => {
        saved.push(blob);
        return "blob:dashboard";
      },
      revokeObjectURL: () => {},
    });
    const download = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await act(async () => exportButton()?.click());
    expect(text(container.querySelector("header"))).toContain(
      "This file includes notes, links, service names and host names.",
    );
    expect(download).not.toHaveBeenCalled();
    const save = [...container.querySelectorAll<HTMLButtonElement>("header button")].find(
      (each) => text(each) === "Download Checkout-on-call.json",
    );
    await act(async () => save?.click());

    expect(download).toHaveBeenCalledOnce();
    const file = await saved[0].text();
    expect(Object.keys(JSON.parse(file)).sort()).toEqual(["definition", "description", "title"]);
    expect(JSON.parse(file).definition).toEqual(dashboard().definition);
    expect(file).not.toContain(DASHBOARD_ID);
    expect(file).not.toContain("Asha");
  });

  it("points an editor at the collector setup under a host chart no machine reports, and not a viewer (AC129)", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    const host = panel("h_1", "top_nodes_trend", { metric: "system.cpu.utilization", count: 5 });
    const empty = json(
      batch({
        h_1: { state: "empty", kind: "top_nodes_trend", read_at: at(0), last_seen_at: null },
      }),
    );
    const views = [];
    for (const role of ["member", "viewer"] as const) {
      stubApi(
        {
          [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard([host])),
          [`GET /dashboards/${DASHBOARD_ID}/context`]: json(context()),
          "GET /dashboard-panels/read": empty,
        },
        role,
      );
      const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
      await waitFor(() => text(section(container, "h_1")).includes("No machine"), "the words");
      views.push(section(container, "h_1"));
    }
    const [member, viewer] = views;

    expect(text(member)).toContain("No machine has reported this metric in this window.");
    expect(member?.querySelector("a")?.getAttribute("href")).toBe("/docs/collector");
    expect(text(member?.querySelector("a") ?? null)).toBe("Read the collector setup");
    expect(text(viewer)).toContain("No machine has reported this metric in this window.");
    expect(viewer?.querySelector("a")).toBeNull();
  });

  it("draws a chart only once it has scrolled into view, and keeps it drawn (FR50)", async () => {
    const watching: Array<{ target: Element; fire: (on: boolean) => void }> = [];
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        callback: IntersectionObserverCallback;
        constructor(callback: IntersectionObserverCallback) {
          this.callback = callback;
        }
        observe(target: Element) {
          watching.push({
            target,
            fire: (on) =>
              this.callback(
                [{ target, isIntersecting: on } as IntersectionObserverEntry],
                this as never,
              ),
          });
        }
        disconnect() {}
        unobserve() {}
      },
    );
    stubApi(viewOf(json(batch({ p_rate: ok() }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => watching.length > 0, "the panels watched");
    await act(async () => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(drawn(section(container, "p_rate"))).toBe(false);

    const rate = watching.find((entry) => entry.target.id === "panel-p_rate");
    await act(async () => rate?.fire(true));
    await waitFor(() => drawn(section(container, "p_rate")), "the chart once seen");
    await act(async () => rate?.fire(false));
    expect(drawn(section(container, "p_rate"))).toBe(true);
  });

  it("tells a viewer nobody has connected a project, and offers no setup (AC105, AC109)", async () => {
    stubApi(
      {
        ...viewOf(
          json(
            batch({
              p_rate: { state: "empty", kind: "project_trend", read_at: at(0), last_seen_at: null },
            }),
          ),
        ),
        [`GET /dashboards/${DASHBOARD_ID}/context`]: json({
          ...context(),
          ever_reported: false,
          last_seen_at: null,
        }),
      },
      "viewer",
    );
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(container).includes("Nobody has connected production yet"),
      "the line",
    );
    expect(text(container)).not.toContain("Set up");
  });
});

describe("the service picker (F20, Step 7, T7.3)", () => {
  const picked = panel("p_pick", "service_trend", { service: { picker: true }, signal: "rate" });
  const withPicker = () => ({
    ...dashboard([picked]),
    definition: { ...dashboard([picked]).definition, service_picker: true },
  });
  const services = json({ services: [{ service_name: "checkout" }, { service_name: "payment" }] });
  const view = (answer: Handler | Handler[]) => ({
    ...viewOf(answer),
    [`GET /dashboards/${DASHBOARD_ID}`]: json(withPicker()),
    "GET /services": services,
  });
  const unset: PanelResult = { state: "needs_service", kind: "service_trend" };

  it("holds the picker in the line that asks for it while no service is chosen, and reads no scan (AC140)", async () => {
    stubApi(view(json(batch({ p_pick: unset }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => text(container).includes("Choose a service to fill this dashboard"),
      "the line",
    );

    const selects = container.querySelectorAll("select");
    expect(selects).toHaveLength(1);
    expect(selects[0].closest("[data-sticky-line]")).toBeNull();
    expect(text(selects[0].parentElement?.parentElement ?? null)).toContain(
      "Choose a service to fill this dashboard",
    );
    expect(text(section(container, "p_pick"))).not.toContain("requests");
  });

  it("keeps it in the sticky line once a service is in the address, still one picker", async () => {
    stubApi(view(json(batch({ p_pick: ok("requests_per_minute", "service_trend") }))));
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?service=checkout`);
    await waitFor(() => container.querySelector("[data-sticky-line] select"), "the sticky picker");

    expect(container.querySelectorAll("select")).toHaveLength(1);
    await waitFor(
      () => container.querySelector<HTMLSelectElement>("select")?.value === "checkout",
      "the address's service chosen",
    );
  });

  it("puts the chosen service in the address and reads once with it, after the pause (AC44)", async () => {
    const calls = stubApi(
      view(json(batch({ p_pick: ok("requests_per_minute", "service_trend") }))),
    );
    const { container } = await mountAt(`/dashboards/${DASHBOARD_ID}?service=checkout`);
    await waitFor(
      () => container.querySelector("[data-sticky-line] select option[value=payment]"),
      "the options",
    );
    const reads = () => calls.filter((call) => call.path === "/dashboard-panels/read");
    const before = reads().length;

    const select = container.querySelector("select") as HTMLSelectElement;
    for (const value of ["payment", "checkout", "payment"]) {
      act(() => {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
    }
    // Stepping through three options sent nothing yet.
    expect(reads()).toHaveLength(before);
    await waitFor(() => reads().length > before, "the read with the new service");

    expect(reads().length).toBe(before + 1);
    expect(searchOf(reads().at(-1) ?? { search: "" }).get("service")).toBe("payment");
    // A choice makes no row: the dashboard is read, never saved.
    expect(calls.some((call) => call.method !== "GET")).toBe(false);
  });
});
