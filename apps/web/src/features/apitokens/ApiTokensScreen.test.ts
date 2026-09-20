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

import { PERMISSIONS_OF, type TestRole } from "@/shared/identity.test-util";
import { ApiTokensScreen } from "./ApiTokensScreen";
import type { ApiTokenSummary } from "./api";

/**
 * The defects these guard (F36):
 *
 * 1. A member offered create or revoke, which the API refuses (BR7).
 * 2. A token created with a scope or expiry the person did not choose — the two
 *    things that decide what a leaked credential costs.
 * 3. A revoked or expired token reading as live, so nobody knows what is in use.
 * 4. A secret shown anywhere but the one panel that has it (BR10).
 */

const PROJECT = "00000000-0000-0000-0000-0000000000a1";

function token(overrides: Partial<ApiTokenSummary>): ApiTokenSummary {
  return {
    id: crypto.randomUUID(),
    name: "CI",
    project_id: null,
    token_hint: "nvt_live_…a9Qz",
    created_by_name: "Raj",
    created_at: "2026-09-01T09:00:00Z",
    expires_at: "2026-12-01T09:00:00Z",
    revoked_at: null,
    last_used_at: "2026-09-20T08:00:00Z",
    requests_today: 12,
    status: "live",
    ...overrides,
  };
}

const TOKENS: ApiTokenSummary[] = [
  token({ name: "Nightly report", project_id: PROJECT }),
  token({ name: "Old CI", status: "revoked", revoked_at: "2026-09-10T09:00:00Z" }),
  token({ name: "Last year", status: "expired", created_by_name: null }),
];

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

function stubApi(role: TestRole, created: string[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname.endsWith("/auth/me")) {
        return json({
          user: {
            id: "u1",
            email: `${role}@example.test`,
            name: role,
            role,
            email_verified_at: null,
            permissions: PERMISSIONS_OF[role],
          },
          organization: { id: "o1", slug: "acme", name: "Acme" },
        });
      }
      if (url.pathname.endsWith("/projects")) {
        return json({
          projects: [
            { id: PROJECT, slug: "production", name: "Production", first_reported_on: null },
          ],
        });
      }
      if (url.pathname.endsWith("/api-tokens")) {
        if ((init?.method ?? "GET") === "GET") return json({ api_tokens: TOKENS });
        created.push(String(init?.body));
        return json({
          api_token: token({ name: "New" }),
          token: "nvt_live_shownoncethenneveragainxyz12",
          notice: "This is the only time this token is shown.",
        });
      }
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
}

async function settle() {
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount() {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings/api-tokens",
    component: () => createElement(QueryClientProvider, { client }, createElement(ApiTokensScreen)),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screen]),
    history: createMemoryHistory({ initialEntries: ["/settings/api-tokens"] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return container;
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function select(container: HTMLElement, label: string): HTMLSelectElement {
  const found = [...container.querySelectorAll("label")].find((each) =>
    each.textContent?.toLowerCase().includes(label.toLowerCase()),
  );
  const field = found?.querySelector("select") ?? found?.parentElement?.querySelector("select");
  if (!field) throw new Error(`no select for ${label}`);
  return field as HTMLSelectElement;
}

const buttons = (element: HTMLElement) =>
  [...element.querySelectorAll("button")].map((button) => button.textContent?.trim());

describe("the API tokens screen", () => {
  it("shows a member every token and no way to change one", async () => {
    stubApi("member");
    const container = await mount();

    expect(container.textContent).toContain("Nightly report");
    expect(container.querySelector('section[aria-label="Create a token"]')).toBeNull();
    const list = container.querySelector<HTMLElement>('ul[aria-label="API tokens"]');
    expect(buttons(list as HTMLElement)).toEqual([]);
  });

  it("marks what is live, revoked and expired, and who made each", async () => {
    stubApi("admin");
    const container = await mount();
    const rows = [...container.querySelectorAll('ul[aria-label="API tokens"] > li')];

    expect(rows).toHaveLength(3);
    expect(rows[0]?.textContent).toContain("live");
    expect(rows[0]?.textContent).toContain("Production");
    expect(rows[1]?.textContent).toContain("revoked");
    expect(rows[2]?.textContent).toContain("expired");
    // A token outlives its maker (AC17), and the list says so rather than lying.
    expect(rows[2]?.textContent).toContain("someone who has left");
    // Only the live one can be withdrawn.
    expect(buttons(rows[0] as HTMLElement)).toEqual(["Revoke"]);
    expect(buttons(rows[1] as HTMLElement)).toEqual([]);
  });

  it("creates a token with the scope and expiry chosen, and shows the secret once", async () => {
    const created: string[] = [];
    stubApi("owner", created);
    const container = await mount();

    const form = container.querySelector<HTMLFormElement>(
      'section[aria-label="Create a token"] form',
    );
    const name = form?.querySelector<HTMLInputElement>('input[name="token-name"]');
    await act(async () => typeInto(name as HTMLInputElement, "Nightly report"));
    const reads = select(container, "Reads");
    const expires = select(container, "Expires in");
    await act(async () => {
      reads.value = PROJECT;
      reads.dispatchEvent(new Event("change", { bubbles: true }));
      expires.value = "30";
      expires.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => form?.requestSubmit());
    await settle();

    expect(JSON.parse(created[0] as string)).toEqual({
      name: "Nightly report",
      project_id: PROJECT,
      expires_in_days: 30,
    });
    const panel = container.querySelector<HTMLElement>('section[aria-label="New token"]');
    expect(panel?.textContent).toContain("nvt_live_shownoncethenneveragainxyz12");
    // The form is out of the way while a secret nothing can reissue is on screen.
    expect(container.querySelector('section[aria-label="Create a token"]')).toBeNull();
  });

  it("offers the assistant setup with a placeholder, never a real token", async () => {
    stubApi("owner");
    const container = await mount();

    const panel = container.querySelector<HTMLElement>(
      'section[aria-label="Connect an AI assistant"]',
    );
    expect(panel?.textContent).toContain("claude mcp add --transport http nexovue");
    expect(panel?.textContent).toContain("nvt_live_…");
    expect(panel?.textContent).not.toContain("nvt_live_shownonce");
  });

  it("is not about one client: every snippet carries the endpoint and the header", async () => {
    stubApi("owner");
    const container = await mount();
    const panel = container.querySelector<HTMLElement>(
      'section[aria-label="Connect an AI assistant"]',
    ) as HTMLElement;
    const picker = panel.querySelector("select") as HTMLSelectElement;
    const clients = [...picker.options].map((option) => option.value);

    expect(clients.length).toBeGreaterThan(3);
    for (const client of clients) {
      await act(async () => {
        picker.value = client;
        picker.dispatchEvent(new Event("change", { bubbles: true }));
      });
      const snippet = panel.querySelector("pre")?.textContent ?? "";
      expect(snippet, client).toContain("/api/mcp/");
      expect(snippet, client).toContain(`Bearer nvt_live_\u2026`);
    }
  });
});
