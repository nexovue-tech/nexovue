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
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";

import type { ProjectDetail } from "./api";
import { DetectionSection } from "./DetectionSection";
import { ProjectSettings } from "./ProjectSettings";

/**
 * Settings → Projects, *Detection* (F6, FR18, FR24, FR26; design *Settings*): a
 * project's sensitivity and time zone, and its false-alarm counts. An admin's only,
 * and hidden from everyone else, as every gated section here is (C10).
 */

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100";
const SETTINGS_PATH = `/api/v1/projects/${PROJECT_ID}/detection`;
const STATS_PATH = "/api/v1/findings/stats";
const PROJECT: ProjectDetail = {
  id: PROJECT_ID,
  slug: "production",
  name: "Production",
  retention_days: 30,
  sampling_config: {},
  masking_config: {},
  created_at: "2026-09-01T09:00:00Z",
};
const STATS = {
  days: 7,
  opened: 4,
  false_alarms: 1,
  judged_services: 3,
  false_finding_rate_per_service_week: 1 / 3,
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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status < 400 ? "application/json" : "application/problem+json" },
  });

function stubApi({
  role = "owner",
  stats = STATS,
  save,
}: {
  role?: TestRole;
  stats?: typeof STATS;
  save?: (body: unknown) => Response;
} = {}) {
  const saved: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/auth/me") return json(identityFor(role));
      if (url.pathname === SETTINGS_PATH && method === "PUT") {
        const body = JSON.parse(String(init?.body));
        saved.push(body);
        return save ? save(body) : json(body);
      }
      if (url.pathname === SETTINGS_PATH) return json({ sensitivity: "normal", timezone: "UTC" });
      if (url.pathname === STATS_PATH) return json(stats);
      // The other sections' lists, all empty: this suite is about one section.
      if (method === "GET") return json({ secrets: [], variables: [], windows: [], checks: [] });
      throw new Error(`no stub for ${method} ${url.pathname}`);
    }),
  );
  return { saved };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mount(component: () => ReturnType<typeof createElement>) {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => createElement(QueryClientProvider, { client }, component()),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  for (let round = 0; round < 8; round += 1) await settle();
  return container;
}

const section = () => mount(() => createElement(DetectionSection, { projectId: PROJECT_ID }));

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();

function select(container: Element, label: string): HTMLSelectElement {
  const found = [...container.querySelectorAll("label")].find((each) =>
    each.textContent?.includes(label),
  );
  const control = found?.querySelector("select") ?? null;
  const byFor = found?.htmlFor
    ? container.querySelector<HTMLSelectElement>(`#${CSS.escape(found.htmlFor)}`)
    : null;
  const chosen = control ?? byFor;
  if (!chosen) throw new Error(`no select labelled "${label}"`);
  return chosen;
}

async function choose(control: HTMLSelectElement, value: string) {
  await act(async () => {
    control.value = value;
    control.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
}

function button(container: Element, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (each) => each.textContent?.trim() === label,
  );
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

async function click(element: HTMLElement) {
  await act(async () => element.click());
  for (let round = 0; round < 4; round += 1) await settle();
}

describe("the Detection section", () => {
  it("reads the sensitivity, the zone and the false alarms against the budget", async () => {
    stubApi();
    const container = await section();

    expect(text(container)).toContain("Detection");
    expect(select(container, "Sensitivity").value).toBe("normal");
    expect(select(container, "Time zone").value).toBe("UTC");
    expect(text(container)).toContain(
      "In the last 7 days, 4 findings opened and 1 was marked a false alarm: 0.33 false findings per service per week, against a budget of 1.",
    );
  });

  it("says when nothing has been judged, rather than a rate of zero", async () => {
    stubApi({
      stats: {
        days: 7,
        opened: 0,
        false_alarms: 0,
        judged_services: 0,
        false_finding_rate_per_service_week: null,
      } as unknown as typeof STATS,
    });
    const container = await section();

    expect(text(container)).toContain("No service has been judged yet");
  });

  it("saves a new sensitivity and says when it applies", async () => {
    const api = stubApi();
    const container = await section();

    expect(button(container, "Save detection").disabled).toBe(true);
    await choose(select(container, "Sensitivity"), "low");
    await click(button(container, "Save detection"));

    expect(api.saved).toEqual([{ sensitivity: "low", timezone: "UTC" }]);
    expect(text(container)).toContain("Saved. It applies from the next judged minute.");
  });

  it("warns that a new time zone moves the normal to the new clock", async () => {
    const api = stubApi();
    const container = await section();

    await choose(select(container, "Time zone"), "Europe/London");

    expect(text(container)).toContain(
      "Each minute is compared with the same local time on earlier days, so the normal moves to the new clock.",
    );
    await click(button(container, "Save detection"));
    expect(api.saved).toEqual([{ sensitivity: "normal", timezone: "Europe/London" }]);
  });

  it("shows the API's refusal beside the form", async () => {
    stubApi({
      save: () =>
        json(
          {
            type: "about:blank",
            title: "Unprocessable",
            status: 422,
            detail: "Mars/Olympus_Mons is not a time zone this system knows.",
          },
          422,
        ),
    });
    const container = await section();

    await choose(select(container, "Sensitivity"), "high");
    await click(button(container, "Save detection"));

    expect(text(container)).toContain("not a time zone this system knows");
  });

  it.each([
    ["owner", true],
    ["admin", true],
    ["member", false],
    ["viewer", false],
  ] as const)("is shown in a project's settings to an %s: %s", async (role, shown) => {
    stubApi({ role });
    const container = await mount(() =>
      createElement(ProjectSettings, { project: PROJECT, onDeleted: () => {} }),
    );

    expect(text(container).includes("Sensitivity")).toBe(shown);
  });
});
