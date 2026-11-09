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
  PanelReadResponse,
  PanelResult,
  StoredPanel,
} from "@/features/dashboards/api";
import { DashboardEditScreen, NewDashboardScreen } from "@/features/dashboards/DashboardEditScreen";
import { DashboardListScreen } from "@/features/dashboards/DashboardListScreen";
import { DashboardScreen } from "@/features/dashboards/DashboardScreen";
import { MAX_LINKS, MAX_PANELS, MAX_SCANS_PER_READ } from "@/features/dashboards/draft";
import type { DetectionStatus } from "@/shared/detection/headline";
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId, setSelectedRangeId } from "@/shared/selection";
import { dismissToast, getToasts } from "@/shared/toast/toast-store";

// The API's own caps, read as text: this package compiles without node types.
import definitionSource from "../../../api/src/nexovue_api/dashboards/definition.py?raw";
import policySource from "../../../api/src/nexovue_api/ratelimit/policy.py?raw";
import { createQueryClient } from "./query-client";

/**
 * The builder (F20, T3.2 to T3.6, T3.8), with a real router and the console's real query
 * client, at the app layer as the view's test is. Only `fetch` is a table.
 *
 * AC37 (one tab stop per toolbar, arrow keys, named actions, focus kept on a move and at
 * the ends, placed after add and remove, every change announced, Remove undoable), AC143
 * (one live region), AC7 and AC105 (a viewer's edit link is refused, and the view offers a
 * viewer no Edit), AC10 (a conflict keeps the draft on screen and says who saved), AC133
 * (leaving warns only while something differs), AC38 (a template opens as a draft and
 * writes nothing until Save), T3.5's validation summary and links editor, T3.6 mounted,
 * T3.8 (below `lg`: Up and Down, no width, one menu per panel, 44 px controls).
 */

const API_PREFIX = "/api/v1";
const PROJECT_ID = "6a1d0c2e-3b4f-4a5d-9e8f-7c6b5a4d3e2f";
const DASHBOARD_ID = "0190e3f4-0000-7000-8000-000000000001";
const SETTLE_ROUNDS = 60;
const NARROW_QUERY = "(max-width: 63.98rem)";

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
const NOTE = { ...panel("p_note", "note", { text: "Check the queue" }), height: "small" as const };

function dashboard(panels: StoredPanel[] = [RATE, CHECKOUT, NOTE], version = 4): Dashboard {
  return {
    id: DASHBOARD_ID,
    title: "Checkout on-call",
    description: "What the checkout team watches.",
    definition: {
      schema_version: 1,
      refresh_seconds: 60,
      default_range: null,
      service_picker: false,
      links: [],
      panels,
    },
    version,
    owner: "Asha",
    updated_by: "Asha",
    last_viewed_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

const judging: DetectionStatus = {
  state: "judging",
  services_total: 1,
  services_learning: 0,
  services_judged: 1,
  services_delayed: 0,
  services_muted: 0,
  earliest_judging_date: null,
  not_running_since: null,
  delayed_since: null,
  open_findings: 0,
  services_too_many: 0,
  judged_service_limit: 50,
};

const context: DashboardContext = {
  project: { id: PROJECT_ID, name: "production", timezone: "UTC", retention: null },
  window_start: new Date().toISOString(),
  window_end: new Date().toISOString(),
  detection: judging,
  ever_reported: true,
  last_seen_at: null,
  findings: [],
  findings_truncated: false,
  open_findings: { total: 0, by_service: {} },
};

const ok = (kind = "project_trend"): PanelResult => ({
  state: "ok",
  kind,
  read_at: new Date().toISOString(),
  bucket_seconds: 60,
  data_starts_at: null,
  data_ends_at: null,
  series: [{ label: "requests_per_minute", points: [[new Date().toISOString(), 10]] }],
});

const answer = (panels: Record<string, PanelResult>): PanelReadResponse => ({
  correlation_id: "corr-9",
  window_start: new Date().toISOString(),
  window_end: new Date().toISOString(),
  panels,
});

type Handler = (body: unknown) => Response;
const json =
  (body: unknown, status = 200): Handler =>
  () =>
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: {
        "content-type": status >= 400 ? "application/problem+json" : "application/json",
      },
    });

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function stubApi(handlers: Record<string, Handler>, role: TestRole = "member"): Call[] {
  const calls: Call[] = [];
  const all: Record<string, Handler> = {
    "GET /auth/me": json(identityFor(role)),
    "GET /services": json({ services: [{ service_name: "checkout" }] }),
    "POST /dashboard-panels/preview": json(
      answer({ p_rate: ok(), p_checkout: ok("service_trend") }),
    ),
    [`GET /dashboards/${DASHBOARD_ID}/context`]: json(context),
    ...handlers,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const path = url.pathname.slice(API_PREFIX.length);
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, path, body });
      const handler = all[`${method} ${path}`];
      if (!handler) throw new Error(`no stub for ${method} ${path}`);
      return handler(body);
    }),
  );
  return calls;
}

const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  for (const toast of getToasts()) dismissToast(toast.id);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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
  const fresh = createRoute({
    getParentRoute: () => rootRoute,
    path: "/dashboards/new",
    validateSearch: (search: Record<string, unknown>) => ({
      template: typeof search.template === "string" ? search.template : undefined,
    }),
    component: () =>
      createElement(NewDashboardScreen, {
        // The app's registered routes type this; a test tree is not registered.
        templateId: (fresh.useSearch() as { template?: string }).template,
      }),
  });
  const view = createRoute({
    getParentRoute: () => rootRoute,
    path: "/dashboards/$dashboardId",
    component: () => createElement(DashboardScreen, { dashboardId: DASHBOARD_ID }),
  });
  const edit = createRoute({
    getParentRoute: () => rootRoute,
    path: "/dashboards/$dashboardId/edit",
    component: () => createElement(DashboardEditScreen, { dashboardId: DASHBOARD_ID }),
  });
  const service = createRoute({
    getParentRoute: () => rootRoute,
    path: "/services/$name",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([list, fresh, view, edit, service]),
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
  return { container, router };
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
const named = (container: Element, name: string) =>
  container.querySelector<HTMLButtonElement>(`button[aria-label="${name}"]`);
const button = (container: Element, label: string) =>
  [...container.querySelectorAll("button")].find((each) => text(each) === label);
const liveRegions = (container: Element) =>
  container.querySelectorAll('[aria-live], [role="status"], [role="log"]');
const live = (container: Element) => text(container.querySelector("[data-live-region]"));
const order = (container: Element) =>
  [...container.querySelectorAll("section[data-panel-id]")].map((section) =>
    section.getAttribute("data-panel-id"),
  );

function field(container: Element, label: string): HTMLInputElement | HTMLSelectElement {
  const found = [...container.querySelectorAll("label")].find((each) => text(each) === label);
  const id = found?.getAttribute("for");
  const control = id
    ? container.querySelector<HTMLInputElement | HTMLSelectElement>(`#${CSS.escape(id)}`)
    : null;
  if (!control) throw new Error(`no field labelled ${label}`);
  return control;
}

function typeInto(control: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype =
    control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(control, value);
  control.dispatchEvent(
    new Event(control instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }),
  );
}

function press(target: Element | null, key: string) {
  act(() => {
    target?.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
}

async function openEditor(handlers: Record<string, Handler> = {}, role: TestRole = "member") {
  const calls = stubApi(
    { [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard()), ...handlers },
    role,
  );
  const mountedAt = await mountAt(`/dashboards/${DASHBOARD_ID}/edit`);
  return { ...mountedAt, calls };
}

async function ready(container: Element) {
  await waitFor(() => container.querySelector('[role="toolbar"]'), "the builder");
}

describe("the builder's caps match the API's", () => {
  it("holds panels, reads and links to the validator's and the policy's own numbers", () => {
    const constant = (source: string, name: string) =>
      Number(new RegExp(`${name}: Final = (\\d+)`).exec(source)?.[1]);
    expect(MAX_PANELS).toBe(constant(definitionSource, "MAX_PANELS"));
    expect(MAX_LINKS).toBe(constant(definitionSource, "MAX_LINKS"));
    expect(MAX_SCANS_PER_READ).toBe(constant(policySource, "MAX_SCANS_PER_READ"));
  });
});

describe("a toolbar per panel, by keyboard alone (AC37)", () => {
  it("has one tab stop per toolbar, and arrow keys, Home and End move along it", async () => {
    const { container } = await openEditor();
    await ready(container);

    const toolbars = [...container.querySelectorAll('[role="toolbar"]')];
    expect(toolbars).toHaveLength(3);
    for (const toolbar of toolbars) {
      expect(toolbar.querySelectorAll('button[tabindex="0"]')).toHaveLength(1);
    }
    const [, checkout] = toolbars;
    expect(checkout.getAttribute("aria-label")).toBe("checkout · Latency actions");
    const first = checkout.querySelector<HTMLButtonElement>('button[tabindex="0"]');
    act(() => first?.focus());

    press(document.activeElement, "ArrowRight");
    expect(document.activeElement?.getAttribute("aria-label")).toBe(
      "Move checkout · Latency later",
    );
    expect(document.activeElement?.getAttribute("tabindex")).toBe("0");
    expect(first?.getAttribute("tabindex")).toBe("-1");

    press(document.activeElement, "End");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Remove checkout · Latency");
    press(document.activeElement, "Home");
    expect(document.activeElement).toBe(first);
  });

  it("moves a panel, keeps focus on the same action, and says where it went", async () => {
    const { container } = await openEditor();
    await ready(container);

    const later = named(container, "Move checkout · Latency later");
    act(() => later?.focus());
    act(() => later?.click());

    expect(order(container)).toEqual(["p_rate", "p_note", "p_checkout"]);
    expect(document.activeElement?.getAttribute("aria-label")).toBe(
      "Move checkout · Latency later",
    );
    expect(live(container)).toBe("checkout · Latency moved to position 3 of 3");
  });

  it("at an end, the action stays focusable, does nothing, and says so", async () => {
    const { container } = await openEditor();
    await ready(container);

    const earlier = named(container, "Move Rate earlier");
    expect(earlier?.getAttribute("aria-disabled")).toBe("true");
    expect(earlier?.disabled).toBe(false);
    act(() => earlier?.focus());
    act(() => earlier?.click());

    expect(order(container)).toEqual(["p_rate", "p_checkout", "p_note"]);
    expect(document.activeElement).toBe(earlier);
    expect(live(container)).toBe("Rate is already first");
  });

  it("widens a panel and says its new width", async () => {
    const { container } = await openEditor();
    await ready(container);

    act(() => named(container, "Make checkout · Latency wider")?.click());

    expect(live(container)).toBe("checkout · Latency is now 6 wide");
  });

  it("removes a panel, puts focus on what took its place, and undo brings it back", async () => {
    const { container } = await openEditor();
    await ready(container);

    act(() => named(container, "Remove checkout · Latency")?.click());

    expect(order(container)).toEqual(["p_rate", "p_note"]);
    expect(live(container)).toBe("checkout · Latency removed. Undo available");
    expect(document.activeElement?.closest("section")?.getAttribute("data-panel-id")).toBe(
      "p_note",
    );

    act(() => button(container, "Undo")?.click());
    expect(order(container)).toEqual(["p_rate", "p_checkout", "p_note"]);
    expect(live(container)).toBe("checkout · Latency restored at position 2 of 3");
    expect(document.activeElement?.closest("section")?.getAttribute("data-panel-id")).toBe(
      "p_checkout",
    );
  });

  it("removing the last panel puts focus on Add a panel", async () => {
    const { container } = await openEditor();
    await ready(container);

    act(() => named(container, "Remove Note")?.click());

    expect(document.activeElement).toBe(button(container, "Add a panel"));
  });

  it("duplicates beside the original and puts focus on the copy", async () => {
    const { container } = await openEditor();
    await ready(container);

    act(() => named(container, "Duplicate Rate")?.click());

    expect(order(container)).toEqual(["p_rate", "p_1", "p_checkout", "p_note"]);
    expect(document.activeElement?.closest("section")?.getAttribute("data-panel-id")).toBe("p_1");
    expect(live(container)).toBe("Rate duplicated at position 2 of 4");
  });

  it("has exactly one live region on the page (AC143)", async () => {
    const { container } = await openEditor();
    await ready(container);
    await waitFor(() => container.querySelector('[role="img"]'), "the previewed charts");

    expect(liveRegions(container)).toHaveLength(1);
  });

  it("lays panels out as the read view does, at lg and at xl (FR49)", async () => {
    const { container } = await openEditor();
    await ready(container);

    const classes = container.querySelector('section[data-panel-id="p_rate"]')?.className ?? "";
    // A chart of width 4 spans 6 between lg and xl, and its own 4 from xl.
    expect(classes).toContain("lg:col-span-6");
    expect(classes).toContain("xl:col-span-4");
  });

  it("states what the dashboard costs against its caps", async () => {
    const { container } = await openEditor();
    await ready(container);

    expect(text(container)).toContain("3 of 30 panels · 2 of 40 reads");
  });
});

describe("the draft reads once on opening, then only on Apply", () => {
  it("previews the stored draft in place in one read, and a move reads nothing", async () => {
    const { container, calls } = await openEditor();
    await ready(container);
    await waitFor(() => container.querySelector('[role="img"]'), "the previewed charts");

    const previews = () => calls.filter((call) => call.path === "/dashboard-panels/preview");
    expect(previews()).toHaveLength(1);
    expect((previews()[0].body as { definition: unknown }).definition).toEqual(
      dashboard().definition,
    );
    act(() => named(container, "Move Rate later")?.click());
    expect(previews()).toHaveLength(1);
  });

  it("adds a panel through the sheet, with focus on it and the change announced", async () => {
    const { container } = await openEditor();
    await ready(container);

    act(() => button(container, "Add a panel")?.click());
    const sheet = container.querySelector("dialog");
    expect(sheet).not.toBeNull();
    await waitFor(() => sheet?.querySelector("option[value='checkout']"), "the services");
    act(() => typeInto(field(sheet as Element, "Service"), "checkout"));
    act(() => button(sheet as Element, "Add to dashboard")?.click());

    expect(container.querySelector("dialog")).toBeNull();
    expect(order(container)).toEqual(["p_rate", "p_checkout", "p_note", "p_1"]);
    expect(live(container)).toBe("checkout · Rate added at position 4 of 4");
    expect(document.activeElement?.closest("section")?.getAttribute("data-panel-id")).toBe("p_1");
    expect(text(container)).toContain("4 of 30 panels · 2 of 40 reads");
  });
});

describe("saving (T3.5)", () => {
  it("opens a template as a draft, writes nothing until Save, then saves once (AC38)", async () => {
    const template = dashboard([RATE]);
    const calls = stubApi({
      "GET /dashboards/templates": json({
        templates: [
          {
            id: "start_from_overview",
            name: "Start from Overview",
            document: { title: "Overview", description: "", definition: template.definition },
          },
        ],
      }),
      "POST /dashboards": json(dashboard([RATE], 1), 201),
      [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard([RATE], 1)),
      "GET /dashboard-panels/read": json(answer({ p_rate: ok() })),
    });
    const { container } = await mountAt("/dashboards/new?template=start_from_overview");
    await ready(container);

    expect((field(container, "Title") as HTMLInputElement).value).toBe("Overview");
    expect(calls.some((call) => call.method === "POST" && call.path === "/dashboards")).toBe(false);
    await act(async () => button(container, "Save")?.click());

    await waitFor(
      () => calls.some((call) => call.path === "/dashboard-panels/read"),
      "the saved dashboard",
    );
    expect(
      calls.filter((call) => call.method === "POST" && call.path === "/dashboards"),
    ).toHaveLength(1);
  });

  it("saves from the version it read, with the links the editor holds", async () => {
    const { container, calls } = await openEditor({
      [`PUT /dashboards/${DASHBOARD_ID}`]: json(dashboard(undefined, 5)),
      "GET /dashboard-panels/read": json(answer({ p_rate: ok() })),
    });
    await ready(container);

    act(() => button(container, "Add a link")?.click());
    act(() => typeInto(field(container, "Label"), "Runbook"));
    act(() => typeInto(field(container, "Address"), "https://wiki.example/checkout"));
    expect(text(container)).toContain("Opens wiki.example");
    await act(async () => button(container, "Save")?.click());

    await waitFor(() => calls.some((call) => call.method === "PUT"), "the save");
    const [put] = calls.filter((call) => call.method === "PUT");
    expect(put.body).toMatchObject({
      version: 4,
      title: "Checkout on-call",
      definition: { links: [{ label: "Runbook", url: "https://wiki.example/checkout" }] },
    });
  });

  it("on a conflict, says who saved, keeps the draft on screen, and offers reload or a copy (AC10)", async () => {
    const { container } = await openEditor({
      [`PUT /dashboards/${DASHBOARD_ID}`]: json(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail:
            "Asha saved this dashboard since you opened it. Reload it, or save yours as a copy.",
        },
        409,
      ),
    });
    await ready(container);

    act(() => typeInto(field(container, "Title"), "Checkout, renamed"));
    await act(async () => button(container, "Save")?.click());
    await waitFor(() => text(container).includes("Asha saved this dashboard"), "the conflict");

    expect((field(container, "Title") as HTMLInputElement).value).toBe("Checkout, renamed");
    expect(button(container, "Reload it")).toBeDefined();
    expect(button(container, "Save as a copy")).toBeDefined();
  });

  it("lists what the API refused in one summary", async () => {
    const { container } = await openEditor({
      [`PUT /dashboards/${DASHBOARD_ID}`]: json(
        {
          type: "about:blank",
          title: "Validation failed",
          status: 422,
          errors: [{ field: "body.definition.links.0.url", reason: "must be an https address" }],
        },
        422,
      ),
    });
    await ready(container);

    act(() => typeInto(field(container, "Title"), "Renamed"));
    await act(async () => button(container, "Save")?.click());
    await waitFor(() => container.querySelector("[data-summary] li"), "the summary");

    expect(text(container.querySelector("[data-summary]"))).toContain(
      "Link 1 address: must be an https address",
    );
  });

  it("refuses an empty title before anything is sent", async () => {
    const { container, calls } = await openEditor();
    await ready(container);

    act(() => typeInto(field(container, "Title"), "   "));
    await act(async () => button(container, "Save")?.click());

    expect(text(container.querySelector("[data-summary]"))).toContain(
      "Give the dashboard a title.",
    );
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
  });
});

describe("leaving (AC133)", () => {
  it("warns while something differs from what is stored", async () => {
    const { container } = await openEditor();
    await ready(container);

    act(() => typeInto(field(container, "Title"), "Changed"));
    await act(async () => button(container, "Cancel")?.click());

    await waitFor(() => text(document.body).includes("Leave without saving?"), "the prompt");
  });

  it("does not warn for a change undone by hand", async () => {
    const { container, router } = await openEditor({
      "GET /dashboard-panels/read": json(answer({ p_rate: ok() })),
    });
    await ready(container);

    act(() => named(container, "Move Rate later")?.click());
    act(() => named(container, "Move Rate earlier")?.click());
    await act(async () => button(container, "Cancel")?.click());

    await waitFor(
      () => router.state.location.pathname === `/dashboards/${DASHBOARD_ID}`,
      "the view",
    );
    expect(text(document.body)).not.toContain("Leave without saving?");
  });
});

describe("who may edit (AC7, AC105)", () => {
  it("refuses a viewer's edit link, offering nothing that would fail", async () => {
    const { container } = await openEditor({}, "viewer");
    await waitFor(() => text(container).includes("cannot open this"), "the refusal");

    expect(button(container, "Save")).toBeUndefined();
    expect(container.querySelector('[role="toolbar"]')).toBeNull();
  });

  it("offers Edit on the view to an editor, and nothing to a viewer", async () => {
    const view = {
      [`GET /dashboards/${DASHBOARD_ID}`]: json(dashboard()),
      "GET /dashboard-panels/read": json(answer({ p_rate: ok() })),
    };
    stubApi(view, "member");
    const editor = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(
      () => editor.container.querySelector(`a[href="/dashboards/${DASHBOARD_ID}/edit"]`),
      "the edit link",
    );
    act(() => {
      for (const root of mounted.splice(0)) root.unmount();
    });
    document.body.replaceChildren();

    stubApi(view, "viewer");
    const viewer = await mountAt(`/dashboards/${DASHBOARD_ID}`);
    await waitFor(() => text(viewer.container).includes("Checkout on-call"), "the view");
    expect(viewer.container.querySelector(`a[href="/dashboards/${DASHBOARD_ID}/edit"]`)).toBeNull();
  });

  it("mounts delete on the builder (T3.6)", async () => {
    const { container } = await openEditor();
    await ready(container);

    expect(button(container, "Delete this dashboard")).toBeDefined();
  });
});

describe("below lg (T3.8, AC108)", () => {
  function narrow() {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === NARROW_QUERY,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }));
  }

  it("reads moves as Up and Down, hides width, and puts the rest in one menu per panel", async () => {
    narrow();
    const { container } = await openEditor();
    await ready(container);

    expect(named(container, "Move checkout · Latency up")).not.toBeNull();
    expect(named(container, "Move checkout · Latency down")).not.toBeNull();
    expect(named(container, "Make checkout · Latency wider")).toBeNull();
    const more = named(container, "More actions for checkout · Latency");
    expect(more?.getAttribute("aria-expanded")).toBe("false");

    act(() => more?.click());
    expect(more?.getAttribute("aria-expanded")).toBe("true");
    expect(named(container, "Remove checkout · Latency")).not.toBeNull();
    expect(named(container, "Make Rate taller")).toBeNull();

    act(() => named(container, "Move checkout · Latency up")?.click());
    expect(order(container)).toEqual(["p_checkout", "p_rate", "p_note"]);
  });

  it("makes every toolbar control at least 44 px below md", async () => {
    narrow();
    const { container } = await openEditor();
    await ready(container);

    for (const control of container.querySelectorAll('[role="toolbar"] button')) {
      expect(control.className).toContain("min-h-11");
      expect(control.className).toContain("min-w-11");
    }
  });
});
