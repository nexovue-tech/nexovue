// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { CheckVariablesSection } from "./CheckVariablesSection";

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100";
const VARIABLES_PATH = `/api/v1/projects/${PROJECT_ID}/check-variables`;

interface StoredVariable {
  name: string;
  value: string;
  updated_at: string;
  used_by_checks: number;
}

const BASE_URL: StoredVariable = {
  name: "base_url",
  value: "https://shop.example.com",
  updated_at: "2026-09-12T08:00:00.000Z",
  used_by_checks: 1,
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

/** Keeps what was written, so the refetch after a save answers with the new value. */
function stubApi(initial: StoredVariable[] = [BASE_URL]) {
  const variables = initial.map((variable) => ({ ...variable }));
  const writes: Array<{ path: string; body: unknown }> = [];
  const deleted: string[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname === VARIABLES_PATH) return json({ variables }, 200);
      const name = decodeURIComponent(url.pathname.slice(VARIABLES_PATH.length + 1));
      if (url.pathname.startsWith(`${VARIABLES_PATH}/`) && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { value: string };
        writes.push({ path: url.pathname, body });
        const existing = variables.findIndex((variable) => variable.name === name);
        const written = {
          name,
          value: body.value,
          updated_at: new Date().toISOString(),
          used_by_checks: variables[existing]?.used_by_checks ?? 0,
        };
        if (existing === -1) variables.push(written);
        else variables[existing] = written;
        return json(written, 200);
      }
      if (url.pathname.startsWith(`${VARIABLES_PATH}/`) && init?.method === "DELETE") {
        deleted.push(url.pathname);
        return new Response(null, { status: 204 });
      }
      throw new Error(`no stub for ${init?.method ?? "GET"} ${url.pathname}`);
    }),
  );
  return { writes, deleted };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** The section, and a link out of it — the navigation the unsaved-changes guard holds. */
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
        createElement(CheckVariablesSection, { projectId: PROJECT_ID }),
        createElement(Link, { to: "/elsewhere" }, "Go elsewhere"),
      ),
  });
  const elsewhereRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/elsewhere",
    component: () => createElement("p", null, "Somewhere else"),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([settingsRoute, elsewhereRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  await settle();
  return { container, router };
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

function findButton(container: HTMLElement, label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
}

function buttonLabelled(container: HTMLElement, label: string): HTMLButtonElement {
  const found = findButton(container, label);
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

async function click(element: HTMLElement) {
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
  await settle();
}

async function leave(container: HTMLElement) {
  const link = container.querySelector("a");
  if (!link) throw new Error("the harness lost its link");
  await click(link);
}

const VALUE_FIELD = "variable-value-base_url";

describe("the variables table", () => {
  it("shows each value, because a variable is not a secret, and says so", async () => {
    stubApi();
    const { container } = await mountSection();

    expect(field(container, VALUE_FIELD).value).toBe("https://shop.example.com");
    expect(container.textContent).toContain("{{base_url}}");
    expect(container.textContent).toContain("visible to anyone who can read this project");
  });

  it("says why it is empty and what to do", async () => {
    stubApi([]);
    const { container } = await mountSection();

    expect(container.textContent).toContain("This project has no check variables");
    expect(container.textContent).toContain("Add one below");
  });
});

describe("editing a value in place", () => {
  it("saves the one row that changed, under its own name", async () => {
    const api = stubApi();
    const { container } = await mountSection();

    await act(async () => typeInto(field(container, VALUE_FIELD), "https://staging.example.com"));
    await click(buttonLabelled(container, "Save"));

    expect(api.writes).toEqual([
      { path: `${VARIABLES_PATH}/base_url`, body: { value: "https://staging.example.com" } },
    ]);
    expect(field(container, VALUE_FIELD).value).toBe("https://staging.example.com");
    expect(findButton(container, "Save")).toBeUndefined();
    expect(container.textContent).toContain("Saved as the value of {{base_url}}");
  });

  it("holds a navigation while a value is unsaved, naming what is at risk", async () => {
    stubApi();
    const { container, router } = await mountSection();

    await act(async () => typeInto(field(container, VALUE_FIELD), "https://staging.example.com"));
    await leave(container);

    const prompt = container.querySelector('[role="alertdialog"]');
    expect(prompt?.textContent).toContain("this project's check variables");
    expect(router.state.location.pathname).toBe("/");
  });

  it("lets the reader leave once the change is reverted", async () => {
    stubApi();
    const { container, router } = await mountSection();

    await act(async () => typeInto(field(container, VALUE_FIELD), "https://staging.example.com"));
    await click(buttonLabelled(container, "Revert"));
    await leave(container);

    expect(container.querySelector('[role="alertdialog"]')).toBeNull();
    expect(router.state.location.pathname).toBe("/elsewhere");
  });

  it("lets the reader leave once the change is saved", async () => {
    stubApi();
    const { container, router } = await mountSection();

    await act(async () => typeInto(field(container, VALUE_FIELD), "https://staging.example.com"));
    await click(buttonLabelled(container, "Save"));
    await leave(container);

    expect(router.state.location.pathname).toBe("/elsewhere");
  });
});

describe("adding and deleting", () => {
  it("adds a dotted name, as Postman writes them", async () => {
    const api = stubApi();
    const { container } = await mountSection();

    await act(async () => typeInto(field(container, "variable-name"), "order.id"));
    await act(async () => typeInto(field(container, "variable-value"), "42"));
    await click(buttonLabelled(container, "Add variable"));

    expect(api.writes).toEqual([{ path: `${VARIABLES_PATH}/order.id`, body: { value: "42" } }]);
    expect(container.textContent).toContain("Added order.id");
  });

  it("refuses to add over an existing name", async () => {
    const api = stubApi();
    const { container } = await mountSection();

    await act(async () => typeInto(field(container, "variable-name"), "base_url"));

    expect(container.textContent).toContain("base_url already exists");
    expect(buttonLabelled(container, "Add variable").disabled).toBe(true);
    expect(api.writes).toEqual([]);
  });

  it("names {{name}} and what breaks before a delete can be confirmed", async () => {
    const api = stubApi();
    const { container } = await mountSection();

    await click(buttonLabelled(container, "Delete"));

    expect(container.textContent).toContain(
      "1 check uses {{base_url}} and will become misconfigured",
    );

    await click(buttonLabelled(container, "Yes, delete base_url"));

    expect(api.deleted).toEqual([`${VARIABLES_PATH}/base_url`]);
  });

  it("says so when no check uses the variable", async () => {
    stubApi([{ ...BASE_URL, used_by_checks: 0 }]);
    const { container } = await mountSection();

    await click(buttonLabelled(container, "Delete"));

    expect(container.textContent).toContain("No check uses {{base_url}}.");
  });
});
