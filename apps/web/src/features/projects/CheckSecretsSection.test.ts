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

import { CheckSecretsSection } from "./CheckSecretsSection";

/**
 * The defect this guards above all others is a secret's value reaching the page (AC17):
 * rendered in the list, pre-filled into a replacement field, or echoed in a success
 * message. So the value typed here is a unique marker, and the page is searched for it.
 */

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0100";
const SECRETS_PATH = `/api/v1/projects/${PROJECT_ID}/check-secrets`;
const MARKER = "marker-7f3c9a1e-never-rendered";
const KEY_MISSING =
  "Check secrets are not available on this deployment, so nothing was stored. The operator needs to configure a check secret key.";

const STORED_SECRET = {
  name: "API_TOKEN",
  created_at: "2026-09-10T08:00:00.000Z",
  updated_at: "2026-09-12T08:00:00.000Z",
  used_by_checks: 3,
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

const problem = (status: number, detail: string) =>
  new Response(JSON.stringify({ type: "about:blank", title: "Unavailable", status, detail }), {
    status,
    headers: { "content-type": "application/problem+json" },
  });

function stubApi(options: { secrets?: (typeof STORED_SECRET)[]; keyMissing?: boolean } = {}) {
  const secrets = [...(options.secrets ?? [STORED_SECRET])];
  const writes: Array<{ path: string; body: unknown }> = [];
  const deleted: string[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname === SECRETS_PATH) return json({ secrets }, 200);
      if (url.pathname.startsWith(`${SECRETS_PATH}/`) && init?.method === "PUT") {
        writes.push({ path: url.pathname, body: JSON.parse(String(init.body)) });
        if (options.keyMissing) return problem(503, KEY_MISSING);
        const name = decodeURIComponent(url.pathname.slice(SECRETS_PATH.length + 1));
        const summary = {
          name,
          created_at: STORED_SECRET.created_at,
          updated_at: new Date().toISOString(),
          used_by_checks: 0,
        };
        if (!secrets.some((secret) => secret.name === name)) secrets.push(summary);
        return json(summary, 200);
      }
      if (url.pathname.startsWith(`${SECRETS_PATH}/`) && init?.method === "DELETE") {
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
        createElement(CheckSecretsSection, { projectId: PROJECT_ID }),
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

async function click(button: HTMLButtonElement) {
  await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle();
}

async function submit(input: HTMLInputElement) {
  const form = input.closest("form");
  if (!form) throw new Error("the field is not in a form");
  await act(async () => form.requestSubmit());
  await settle();
  await settle();
}

async function addSecret(container: HTMLElement, name: string, value: string) {
  await act(async () => typeInto(field(container, "secret-name"), name));
  await act(async () => typeInto(field(container, "secret-value"), value));
  await submit(field(container, "secret-name"));
}

describe("the secrets list", () => {
  it("names each secret and how to use it, with nothing that could be a value", async () => {
    stubApi();
    const container = await mountSection();

    expect(container.textContent).toContain("API_TOKEN");
    expect(container.textContent).toContain("{{secret.API_TOKEN}}");
    for (const input of container.querySelectorAll("input")) expect(input.value).toBe("");
  });

  it("says nobody can read a stored value back, owners included", async () => {
    stubApi();
    const container = await mountSection();

    expect(container.textContent).toContain(
      "Nobody can read a stored value back — owners included",
    );
  });

  it("says why it is empty and what to do", async () => {
    stubApi({ secrets: [] });
    const container = await mountSection();

    expect(container.textContent).toContain("This project has no check secrets");
    expect(container.textContent).toContain("Add one below");
  });
});

describe("replacing a secret", () => {
  it("offers an empty password field, never the old value", async () => {
    stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Replace"));

    const replacement = field(container, "replacement-value");
    expect(replacement.value).toBe("");
    expect(replacement.type).toBe("password");
    expect(replacement.getAttribute("autocomplete")).toBe("new-password");
  });

  it("sends only the value, to the secret's own name", async () => {
    const api = stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Replace"));
    await act(async () => typeInto(field(container, "replacement-value"), MARKER));
    await submit(field(container, "replacement-value"));

    expect(api.writes).toEqual([{ path: `${SECRETS_PATH}/API_TOKEN`, body: { value: MARKER } }]);
    expect(container.innerHTML).not.toContain(MARKER);
    expect(container.textContent).toContain("Replaced.");
  });
});

describe("adding a secret", () => {
  it("stores it, says so, and leaves the value nowhere on the page", async () => {
    const api = stubApi();
    const container = await mountSection();

    await addSecret(container, "NEW_TOKEN", MARKER);

    expect(api.writes).toEqual([{ path: `${SECRETS_PATH}/NEW_TOKEN`, body: { value: MARKER } }]);
    expect(container.textContent).toContain("Stored NEW_TOKEN");
    expect(container.innerHTML).not.toContain(MARKER);
    expect(field(container, "secret-value").value).toBe("");
  });

  it("shows the API's own sentence when the deployment cannot store secrets", async () => {
    stubApi({ keyMissing: true });
    const container = await mountSection();

    await addSecret(container, "NEW_TOKEN", MARKER);

    expect(container.textContent).toContain("The secret was not stored");
    expect(container.textContent).toContain(KEY_MISSING);
  });

  it("refuses a name that already exists instead of silently replacing it", async () => {
    const api = stubApi();
    const container = await mountSection();

    await addSecret(container, "API_TOKEN", MARKER);

    expect(api.writes).toEqual([]);
    expect(container.textContent).toContain("API_TOKEN already exists");
  });

  it("refuses a value too short to redact safely before sending it", async () => {
    const api = stubApi();
    const container = await mountSection();

    await addSecret(container, "NEW_TOKEN", "abc");

    expect(api.writes).toEqual([]);
    expect(container.textContent).toContain("At least 4 characters");
  });
});

describe("deleting a secret", () => {
  it("says how many checks break before it can be confirmed", async () => {
    const api = stubApi();
    const container = await mountSection();

    await click(buttonLabelled(container, "Delete"));

    expect(container.textContent).toContain(
      "3 checks use {{secret.API_TOKEN}} and will become misconfigured",
    );
    expect(api.deleted).toEqual([]);

    await click(buttonLabelled(container, "Yes, delete API_TOKEN"));

    expect(api.deleted).toEqual([`${SECRETS_PATH}/API_TOKEN`]);
  });

  it.each([
    [1, "1 check uses {{secret.API_TOKEN}} and will become misconfigured"],
    [0, "No check uses {{secret.API_TOKEN}}."],
  ])("counts %i using checks in words", async (used, sentence) => {
    stubApi({ secrets: [{ ...STORED_SECRET, used_by_checks: used }] });
    const container = await mountSection();

    await click(buttonLabelled(container, "Delete"));

    expect(container.textContent).toContain(sentence);
  });
});
