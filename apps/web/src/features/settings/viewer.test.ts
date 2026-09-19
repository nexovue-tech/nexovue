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
import { setSelectedProjectId } from "@/shared/selection";

import { ApiKeysScreen } from "./ApiKeysScreen";

/**
 * F26, AC4: the ingest keys screen shows keys to everyone and the ways to mint and
 * withdraw them only to an owner or an admin. Paired with the owner, so a renamed label
 * cannot make the viewer case pass by accident.
 */

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a00aa";
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

function stubApi(role: TestRole) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname.replace("/api/v1/", "");
      if (path === "auth/me") return json(identityFor(role));
      if (path === "projects") {
        return json({ projects: [{ id: PROJECT_ID, slug: "production", name: "Production" }] });
      }
      if (path === "system/ingest") {
        return json({ otlp_endpoint: "http://localhost:14318", key_header: "x-nexovue-key" });
      }
      if (path === "site-keys") return json({ site_keys: [] });
      if (path === "api-keys") {
        return json({
          api_keys: [
            {
              kind: "secret",
              id: "k1",
              project_id: PROJECT_ID,
              name: "checkout",
              key_hint: "nvk_live_…abcd",
              created_at: "2026-09-01T09:00:00Z",
              last_used_at: null,
              revoked_at: null,
            },
          ],
        });
      }
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
}

async function mount(
  Screen: () => ReturnType<typeof ApiKeysScreen> = ApiKeysScreen,
  path = "/settings/api-keys",
) {
  setSelectedProjectId(PROJECT_ID);
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path,
    component: () => createElement(QueryClientProvider, { client }, createElement(Screen)),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screen]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return container;
}

const buttons = (element: Element) =>
  [...element.querySelectorAll("button")].map((button) => button.textContent?.trim());

describe("ingest keys, by role", () => {
  it.each(["owner", "viewer"] as const)("for an %s", async (role) => {
    stubApi(role);
    const container = await mount();

    expect(container.textContent).toContain("checkout");
    for (const control of ["Create key", "Rotate", "Revoke now — this key leaked"]) {
      expect(buttons(container).includes(control)).toBe(role === "owner");
    }
  });
});
