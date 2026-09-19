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

import { AcceptInviteScreen } from "./AcceptInviteScreen";

/**
 * The defects these guard (F26): an invite that lands on a bare password form with no
 * word of who sent it or into what; a dead link that reads like a server fault; and a
 * token that travels in a URL the API logs rather than in a body.
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

const reply = (body: unknown, status: number, type = "application/json") =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": type } });

function stubApi(preview: Response) {
  const bodies: unknown[] = [];
  const fetchMock = vi.fn(async (url: URL, init?: RequestInit) => {
    if (url.pathname.endsWith("/auth/me")) {
      return reply(
        { type: "about:blank", title: "Unauthorized", status: 401 },
        401,
        "application/problem+json",
      );
    }
    if (url.pathname.endsWith("/invitations/preview")) {
      bodies.push(typeof init?.body === "string" ? JSON.parse(init.body) : undefined);
      return preview.clone();
    }
    throw new Error(`no stub for ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { bodies, fetch: fetchMock };
}

async function settle() {
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(token: string | undefined) {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path: "/invite",
    component: () =>
      createElement(QueryClientProvider, { client }, createElement(AcceptInviteScreen, { token })),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screen]),
    history: createMemoryHistory({ initialEntries: ["/invite"] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return container;
}

describe("accepting an invite", () => {
  it("says who invited you, into what and as what, before asking for anything", async () => {
    const api = stubApi(
      reply(
        {
          organization: "Acme",
          email: "priya@example.test",
          role: "member",
          invited_by: "Raj",
          expires_at: "2026-09-26T08:00:00Z",
        },
        200,
      ),
    );
    const container = await mount("the-token");

    expect(container.textContent).toContain("Join Acme");
    expect(container.textContent).toContain("Raj invited priya@example.test as a member");
    expect(container.querySelector('input[name="password"]')).not.toBeNull();
    expect(api.bodies).toEqual([{ token: "the-token" }]);
    const urls = api.fetch.mock.calls.map(([url]) => String(url));
    expect(urls.every((url) => !url.includes("the-token"))).toBe(true);
  });

  it("explains a dead link in words, with no form", async () => {
    stubApi(
      reply(
        {
          type: "about:blank",
          title: "Invalid request",
          status: 422,
          detail: "This link is no longer valid. Ask whoever invited you to send a new one.",
        },
        422,
        "application/problem+json",
      ),
    );
    const container = await mount("spent");

    expect(container.textContent).toContain("This invite cannot be used");
    expect(container.textContent).toContain("Ask whoever invited you to send a new one.");
    expect(container.querySelector("form")).toBeNull();
  });

  it("says a link with no token is incomplete rather than posting nothing", async () => {
    const api = stubApi(reply({}, 200));
    const container = await mount(undefined);

    expect(container.textContent).toContain("This link is incomplete");
    expect(api.bodies).toEqual([]);
  });
});
