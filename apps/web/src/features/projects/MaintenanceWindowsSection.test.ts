// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { CheckChoice, MaintenanceWindow } from "./check-settings-api";
import { MaintenanceWindowsSection } from "./MaintenanceWindowsSection";

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100";
const WINDOWS_PATH = "/api/v1/maintenance-windows";
const CHECKS_PATH = "/api/v1/checks";
const CHECKOUT = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0a01";

const CHECKS: CheckChoice[] = [{ id: CHECKOUT, name: "Checkout API", type: "api" }];

const PROJECT_WIDE: MaintenanceWindow = {
  id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0201",
  project_id: PROJECT_ID,
  name: "Provider maintenance",
  starts_at: "2026-09-17T02:00:00.000Z",
  ends_at: "2026-09-17T04:00:00.000Z",
  recurrence: "none",
  repeat_until: null,
  applies_to_all: true,
  check_ids: [],
  active_now: true,
  created_at: "2026-09-15T10:00:00.000Z",
};

const CHOSEN: MaintenanceWindow = {
  ...PROJECT_WIDE,
  id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0202",
  name: "Weekly deploy",
  recurrence: "weekly",
  applies_to_all: false,
  check_ids: [CHECKOUT],
  active_now: false,
};

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function stubApi(
  options: { windows?: MaintenanceWindow[]; checks?: CheckChoice[]; refuse?: unknown } = {},
) {
  const posted: unknown[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname === WINDOWS_PATH && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as MaintenanceWindow;
        posted.push(body);
        if (options.refuse) {
          return new Response(
            JSON.stringify({
              type: "about:blank",
              title: "Unprocessable",
              status: 422,
              detail: "This maintenance window cannot be saved.",
              errors: options.refuse,
            }),
            { status: 422, headers: { "content-type": "application/problem+json" } },
          );
        }
        return json({ ...body, id: "new", active_now: false, created_at: body.starts_at }, 201);
      }
      if (url.pathname === WINDOWS_PATH) return json({ windows: options.windows ?? [] }, 200);
      if (url.pathname === CHECKS_PATH) {
        return json({ checks: options.checks ?? CHECKS, available_locations: ["in-south"] }, 200);
      }
      throw new Error(`no stub for ${init?.method ?? "GET"} ${url.pathname}`);
    }),
  );
  return { posted };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mountSection() {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  const rootRoute = createRootRoute();
  const settingsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () =>
      createElement(
        QueryClientProvider,
        { client },
        createElement(MaintenanceWindowsSection, { projectId: PROJECT_ID }),
      ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([settingsRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  await settle();
  return container;
}

function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function field(container: HTMLElement, name: string): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>(`input[name="${name}"]`);
  if (!input) throw new Error(`no ${name} field on the screen`);
  return input;
}

function buttonLabelled(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

function labelled(container: HTMLElement, text: string): HTMLInputElement {
  const label = [...container.querySelectorAll("label")].find((candidate) =>
    candidate.textContent?.includes(text),
  );
  const input = label?.querySelector("input");
  if (!input) throw new Error(`no input labelled "${text}"`);
  return input;
}

async function click(element: HTMLElement) {
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
  await settle();
}

async function fillWindow(container: HTMLElement, times: { starts: string; ends: string }) {
  await act(async () => typeInto(field(container, "window-name"), "Deploy"));
  await act(async () => typeInto(field(container, "starts_at"), times.starts));
  await act(async () => typeInto(field(container, "ends_at"), times.ends));
}

describe("the maintenance windows list", () => {
  it("says why it is empty and offers to schedule one", async () => {
    stubApi();
    const container = await mountSection();

    expect(container.textContent).toContain("No maintenance windows");
    expect(container.textContent).toContain("Schedule one before planned work");
    buttonLabelled(container, "Schedule a window");
  });

  it("says a project-wide window covers every check, and marks one in effect in words", async () => {
    stubApi({ windows: [PROJECT_WIDE] });
    const container = await mountSection();

    expect(container.textContent).toContain("Provider maintenance");
    expect(container.textContent).toContain("In effect now");
    expect(container.textContent).toContain("Every check in this project");
    expect(container.textContent).toContain("Does not repeat");
  });

  it("names the checks a chosen-checks window covers, and does not mark it in effect", async () => {
    stubApi({ windows: [CHOSEN] });
    const container = await mountSection();

    expect(container.textContent).toContain("1 check: Checkout API");
    expect(container.textContent).toContain("Repeats every week, with no end date");
    expect(container.textContent).not.toContain("In effect now");
  });

  it("explains that a failing check changes no state and its runs are still recorded", async () => {
    stubApi();
    const container = await mountSection();

    expect(container.textContent).toContain("a failing check changes no state");
    expect(container.textContent).toContain("Its runs are still recorded");
  });
});

describe("scheduling a window", () => {
  it("refuses an end before the start beside the field, without asking the API", async () => {
    const api = stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Schedule a window"));
    await fillWindow(container, { starts: "2026-09-20T04:00", ends: "2026-09-20T02:00" });
    await click(buttonLabelled(container, "Schedule window"));

    expect(container.textContent).toContain("Must be after the start.");
    expect(api.posted).toEqual([]);
  });

  it("summarises several problems at once", async () => {
    const api = stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Schedule a window"));
    await click(buttonLabelled(container, "Schedule window"));

    const summary = container.querySelector('[role="alert"]');
    expect(summary?.textContent).toContain("3 fields need attention");
    expect(summary?.textContent).toContain("Name: Name the window.");
    expect(api.posted).toEqual([]);
  });

  it("refuses a daily window as long as a day", async () => {
    const api = stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Schedule a window"));
    await fillWindow(container, { starts: "2026-09-20T02:00", ends: "2026-09-21T02:00" });
    const repeats = container.querySelector("select");
    if (!repeats) throw new Error("no recurrence select");
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
      setValue?.call(repeats, "daily");
      repeats.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await click(buttonLabelled(container, "Schedule window"));

    expect(container.textContent).toContain("A daily window must be shorter than one day");
    expect(api.posted).toEqual([]);
  });

  it("covers the whole project by default and sends instants with an offset", async () => {
    const api = stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Schedule a window"));
    await fillWindow(container, { starts: "2026-09-20T02:00", ends: "2026-09-20T04:00" });
    await click(buttonLabelled(container, "Schedule window"));

    expect(api.posted).toEqual([
      {
        project_id: PROJECT_ID,
        name: "Deploy",
        starts_at: new Date("2026-09-20T02:00").toISOString(),
        ends_at: new Date("2026-09-20T04:00").toISOString(),
        recurrence: "none",
        repeat_until: null,
        applies_to_all: true,
        check_ids: [],
      },
    ]);
    expect(container.textContent).toContain("Scheduled Deploy");
  });

  it("sends only the checks chosen", async () => {
    const api = stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Schedule a window"));
    await fillWindow(container, { starts: "2026-09-20T02:00", ends: "2026-09-20T04:00" });
    await click(labelled(container, "Only the checks I choose"));
    await click(buttonLabelled(container, "Schedule window"));
    expect(container.textContent).toContain(
      "Choose at least one check, or cover the whole project.",
    );
    expect(api.posted).toEqual([]);

    await click(labelled(container, "Checkout API"));
    await click(buttonLabelled(container, "Schedule window"));

    expect(api.posted).toMatchObject([{ applies_to_all: false, check_ids: [CHECKOUT] }]);
  });

  it("says there is nothing to choose in a project with no checks yet", async () => {
    stubApi({ checks: [] });
    const container = await mountSection();

    await click(buttonLabelled(container, "Schedule a window"));
    await click(labelled(container, "Only the checks I choose"));

    expect(container.textContent).toContain("This project has no checks yet");
  });

  it("places the API's own refusal under the field it names", async () => {
    stubApi({ refuse: [{ field: "check_ids", reason: `no check ${CHECKOUT} in this project` }] });
    const container = await mountSection();

    await click(buttonLabelled(container, "Schedule a window"));
    await fillWindow(container, { starts: "2026-09-20T02:00", ends: "2026-09-20T04:00" });
    await click(labelled(container, "Only the checks I choose"));
    await click(labelled(container, "Checkout API"));
    await click(buttonLabelled(container, "Schedule window"));

    expect(container.textContent).toContain(`No check ${CHECKOUT} in this project.`);
    expect(container.textContent).not.toContain("The maintenance window was not saved");
  });
});
