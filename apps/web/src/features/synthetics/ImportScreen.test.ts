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
import { identityFor } from "@/shared/identity.test-util";
import { setSelectedProjectId } from "@/shared/selection";
import { ImportScreen } from "./ImportScreen";
import type { ImportConfirmBody, ImportPlan } from "./import-api";

/**
 * The defects these guard:
 *
 * 1. A credential or a skipped option the import found, not shown before Create (EC5, EC8).
 * 2. A deselected check created anyway.
 * 3. A refusal replaced by a generic message, losing the API's instructions (AC13).
 * 4. Create sent with a secret nobody filled in, which the API would refuse after the fact.
 */

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0003";
const PREVIEW_PATH = "/api/v1/check-imports/preview";
const CONFIRM_PATH = "/api/v1/check-imports/confirm";
const COMMAND = "curl -k -H 'Authorization: Bearer tok-7f3a9c' https://api.example.com/orders";

function apiRequest(url: string) {
  return {
    name: "",
    method: "GET",
    url,
    query: [],
    headers: [],
    body: { kind: "none" as const },
    auth: { kind: "bearer" as const, token_secret: "API_EXAMPLE_COM_TOKEN" },
    assertions: [
      {
        source: "status_code" as const,
        property: "",
        comparison: "less_than" as const,
        target: "400",
      },
    ],
    extractions: [],
    follow_redirects: false,
    timeout_seconds: 10,
  };
}

function planWith(secretValue: string): ImportPlan {
  return {
    source: "curl",
    title: "GET api.example.com/orders",
    servers: [],
    items: [
      {
        key: "GET https://api.example.com/orders",
        name: "GET api.example.com/orders",
        method: "GET",
        url: "https://api.example.com/orders",
        config: { type: "api", request: apiRequest("https://api.example.com/orders") },
        needs_values: [],
        needs_files: [],
        selected: true,
      },
      {
        key: "GET https://api.example.com/health",
        name: "GET api.example.com/health",
        method: "GET",
        url: "https://api.example.com/health",
        config: { type: "api", request: apiRequest("https://api.example.com/health") },
        needs_values: [],
        needs_files: [],
        selected: true,
      },
    ],
    secrets: [
      {
        name: "API_EXAMPLE_COM_TOKEN",
        value: secretValue,
        found_in: "the cURL command: Authorization bearer token",
      },
    ],
    variables: [],
    skipped: [
      {
        item: "-k",
        reason: "certificate verification cannot be turned off",
      },
    ],
  };
}

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status: number, contentType = "application/json") =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": contentType } });

function stubApi(preview: () => Response) {
  const previews: unknown[] = [];
  const confirms: ImportConfirmBody[] = [];
  const fetchMock = vi.fn(async (url: URL, init?: RequestInit) => {
    if (url.pathname === PREVIEW_PATH) {
      previews.push(JSON.parse(String(init?.body)));
      return preview();
    }
    if (url.pathname === CONFIRM_PATH) {
      confirms.push(JSON.parse(String(init?.body)));
      return json({ created: ["c1"] }, 201);
    }
    if (url.pathname.endsWith("/auth/me")) return json(identityFor("member"), 200);
    throw new Error(`no stub for ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { previews, confirms };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mountImport() {
  setSelectedProjectId(PROJECT_ID);
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  const rootRoute = createRootRoute();
  const importRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/synthetics/import",
    component: () => createElement(QueryClientProvider, { client }, createElement(ImportScreen)),
  });
  const listRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/synthetics",
    component: () => createElement("p", null, "Synthetics list"),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([importRoute, listRoute]),
    history: createMemoryHistory({ initialEntries: ["/synthetics/import"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return { container, router };
}

function typeInto(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype =
    field instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

function buttonLabelled(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

async function click(element: HTMLElement) {
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
}

async function previewCommand(container: HTMLElement) {
  const textarea = container.querySelector<HTMLTextAreaElement>('textarea[name="curl"]');
  if (!textarea) throw new Error("no cURL field on the screen");
  await act(async () => typeInto(textarea, COMMAND));
  const form = textarea.closest("form");
  if (!form) throw new Error("the cURL field is not in a form");
  await act(async () => form.requestSubmit());
  await settle();
  await settle();
}

describe("the import screen", () => {
  it("previews a cURL command, listing the secret it found and the option it skipped", async () => {
    const api = stubApi(() => json(planWith("tok-7f3a9c"), 200));
    const { container } = await mountImport();

    await previewCommand(container);

    expect(api.previews).toEqual([{ project_id: PROJECT_ID, source: "curl", content: COMMAND }]);
    expect(container.textContent).toContain("API_EXAMPLE_COM_TOKEN");
    expect(container.textContent).toContain("These become project secrets");
    expect(container.textContent).toContain("-k");
    expect(container.textContent).toContain("certificate verification cannot be turned off");
    expect(api.confirms).toEqual([]);
  });

  it("creates only the chosen checks, then goes to the list", async () => {
    const api = stubApi(() => json(planWith("tok-7f3a9c"), 200));
    const { container, router } = await mountImport();
    await previewCommand(container);

    const second = container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')[1];
    if (!second) throw new Error("no second check to deselect");
    await click(second);
    await click(buttonLabelled(container, "Create checks"));
    await settle();

    expect(api.confirms).toHaveLength(1);
    expect(api.confirms[0]?.checks.map((check) => check.name)).toEqual([
      "GET api.example.com/orders",
    ]);
    expect(api.confirms[0]?.secrets).toEqual([
      { name: "API_EXAMPLE_COM_TOKEN", value: "tok-7f3a9c" },
    ]);
    expect(router.state.location.pathname).toBe("/synthetics");
  });

  it("shows a refusal in the API's own words", async () => {
    const detail =
      "This is a Postman collection v2.0, which cannot be imported. In Postman, open the collection's ⋯ menu → Export, choose Collection v2.1, and import the exported file.";
    stubApi(() =>
      json(
        { type: "about:blank", title: "Validation failed", status: 422, detail },
        422,
        "application/problem+json",
      ),
    );
    const { container } = await mountImport();

    await previewCommand(container);

    expect(container.textContent).toContain(detail);
  });

  it("does not create while a needed secret has no value, and says which", async () => {
    const api = stubApi(() => json(planWith(""), 200));
    const { container, router } = await mountImport();
    await previewCommand(container);

    await click(buttonLabelled(container, "Create checks"));

    expect(api.confirms).toEqual([]);
    expect(container.textContent).toContain(
      "Secret API_EXAMPLE_COM_TOKEN: Enter the value for this secret.",
    );
    expect(router.state.location.pathname).toBe("/synthetics/import");
  });
});
