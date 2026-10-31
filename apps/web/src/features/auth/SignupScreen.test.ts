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

import { SignupScreen } from "./SignupScreen";

/**
 * F31, FR30: where signup is closed — nexovue's cloud, which sets customers up after
 * they pay — the screen says how to buy instead of offering a form the API refuses.
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

function answer(enabled: boolean, deployment: "hosted" | "self-hosted" = "hosted") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      if (url.pathname.endsWith("/system/signup")) {
        return new Response(JSON.stringify({ enabled }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.pathname.endsWith("/system/info")) {
        return new Response(JSON.stringify({ deployment }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
}

async function mount() {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path: "/signup",
    component: () => createElement(QueryClientProvider, { client }, createElement(SignupScreen)),
  });
  const login = createRoute({ getParentRoute: () => rootRoute, path: "/login" });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screen, login]),
    history: createMemoryHistory({ initialEntries: ["/signup"] }),
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

describe("the signup screen", () => {
  it("says how to buy, with no form, where signup is closed", async () => {
    answer(false);
    const container = await mount();

    expect(container.textContent).toContain("nexovue cloud is set up for you");
    expect(container.querySelector("form")).toBeNull();
  });

  it("sends nobody to buy a plan on a customer's own install", async () => {
    answer(false, "self-hosted");
    const container = await mount();

    expect(container.textContent).toContain("Accounts here are by invitation");
    expect(container.textContent).not.toContain("nexovue.com");
    expect(container.querySelector("form")).toBeNull();
  });

  it("offers the form where signup is open", async () => {
    answer(true);
    const container = await mount();

    expect(container.querySelector("form")).not.toBeNull();
  });
});

describe("the account's time zone (F6, D23)", () => {
  it("sends the browser's zone with the signup, so the first project's days are local", async () => {
    const sent: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: URL, init?: RequestInit) => {
        const reply = (body: unknown) =>
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        if (url.pathname.endsWith("/system/signup")) return reply({ enabled: true });
        if (url.pathname.endsWith("/system/info")) return reply({ deployment: "hosted" });
        if (url.pathname.endsWith("/signup") && init?.method === "POST") {
          sent.push(JSON.parse(String(init.body)));
          return new Response("{}", { status: 503 });
        }
        throw new Error(`no stub for ${url.pathname}`);
      }),
    );
    const container = await mount();
    const form = container.querySelector("form");
    if (!form) throw new Error("no form");
    const fill = (name: string, value: string) => {
      const input = form.querySelector<HTMLInputElement>(`input[name="${name}"]`);
      if (!input) throw new Error(`no ${name} field`);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };

    await act(async () => {
      fill("organization_name", "Acme");
      fill("email", "owner@acme.test");
      fill("password", "correct horse battery staple");
    });
    await act(async () => form.requestSubmit());

    expect(sent).toEqual([
      expect.objectContaining({ timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }),
    ]);
  });
});
