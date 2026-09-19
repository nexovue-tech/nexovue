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

import type { Action } from "@/shared/identity";

import type { SupportAccessState } from "./api";
import { SupportAccessScreen } from "./SupportAccessScreen";

/**
 * The defects these guard (F31, FR28):
 *
 * 1. Someone other than an owner offered the consent the API will refuse.
 * 2. A grant sent with a duration the owner did not choose.
 * 3. Access in force with no way to withdraw it from the screen that shows it.
 */

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
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const OWNER: Action[] = ["manage_members", "manage_owners", "grant_support", "read_audit"];
const ADMIN: Action[] = ["manage_members", "read_audit"];

function stubApi(permissions: Action[], state: SupportAccessState, writes: string[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname.endsWith("/auth/me")) {
        return json({
          user: {
            id: "u1",
            email: "raj@acme.test",
            name: "Raj",
            role: permissions.includes("grant_support") ? "owner" : "admin",
            email_verified_at: null,
            permissions,
          },
          organization: { id: "o1", slug: "acme", name: "Acme" },
        });
      }
      if (url.pathname.endsWith("/support-access")) {
        const method = init?.method ?? "GET";
        if (method === "GET") return json(state);
        writes.push(`${method} ${init?.body ?? ""}`.trim());
        return json(null, 204);
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
    path: "/settings/support",
    component: () =>
      createElement(QueryClientProvider, { client }, createElement(SupportAccessScreen)),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screen]),
    history: createMemoryHistory({ initialEntries: ["/settings/support"] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return container;
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (each) => each.textContent?.trim() === label,
  );
  if (!found) throw new Error(`no ${label} button`);
  return found;
}

describe("the Support access screen", () => {
  it("refuses an admin: consent is the owner's", async () => {
    stubApi(ADMIN, { granted_until: null, views: [] });
    const container = await mount();

    expect(container.querySelector('section[aria-label="Access"]')).toBeNull();
    expect(container.textContent).toContain("an owner");
  });

  it("grants for the duration the owner chose", async () => {
    const writes: string[] = [];
    stubApi(OWNER, { granted_until: null, views: [] }, writes);
    const container = await mount();

    expect(container.textContent).toContain("nexovue support cannot view your console.");
    const select = container.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = "168";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => button(container, "Grant access").click());
    await settle();

    expect(writes).toEqual(['POST {"hours":168}']);
  });

  it("offers withdrawal while access is in force, and lists every view", async () => {
    const writes: string[] = [];
    stubApi(
      OWNER,
      {
        granted_until: "2026-09-23T09:00:00Z",
        views: [
          {
            at: "2026-09-20T09:00:00Z",
            staff: "Raj at nexovue",
            viewed: "Priya",
            reason: "checking their alert rule",
            impersonation_id: "i1",
          },
        ],
      },
      writes,
    );
    const container = await mount();

    expect(container.textContent).toContain("Raj at nexovue viewed as Priya");
    expect(container.textContent).toContain("checking their alert rule");
    await act(async () => button(container, "Withdraw now").click());
    await settle();

    expect(writes).toEqual(["DELETE"]);
  });
});
