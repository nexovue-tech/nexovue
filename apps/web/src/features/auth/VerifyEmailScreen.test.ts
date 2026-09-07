// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement, StrictMode } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { VerifyEmailScreen } from "./VerifyEmailScreen";

/**
 * The defect these guard: the token on a verification link is single use, the screen
 * spends it from an effect, and React StrictMode invokes effects twice in development.
 * A naive effect therefore spends the token on the first call and renders "that link did
 * not work" from the second — every customer, every time, only in development, which is
 * the worst place for it to hide.
 *
 * So every mount below is wrapped in StrictMode on purpose. `TOKEN` is 43 URL-safe
 * characters, the shape `OneTimeTokenStore` issues.
 */

const TOKEN = "5FhqYlF0aRDG3Iw8nXCyKZ4Vb2tMuPQeS7jJdRvNzUk";

/** The API's one message for every way a link can fail. It never says which way. */
const LINK_NO_LONGER_VALID =
  "That link is no longer valid. It may have expired or already been used. Request a new one.";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

/** 204: the endpoint answers with no body when the token was good. */
function respondVerified() {
  const fetch = vi.fn(async () => new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

/** The API's own shape: one handler emits RFC 9457 problem+json for every failure. */
function respondRefused() {
  const fetch = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          type: "https://nexovue.dev/problems/validation-failed",
          title: "The request body or parameters are not valid",
          status: 422,
          detail: LINK_NO_LONGER_VALID,
        }),
        { status: 422, headers: { "content-type": "application/problem+json" } },
      ),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

/** The screen links to `/login` and `/traces`, so it is mounted inside a real router. */
async function mountVerifyEmail(token: string | undefined): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const rootRoute = createRootRoute({
    component: () =>
      createElement(QueryClientProvider, { client }, createElement(VerifyEmailScreen, { token })),
  });
  const children = ["/login", "/traces"].map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path, component: () => null }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(children),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(createElement(StrictMode, null, createElement(RouterProvider, { router }))),
  );
  // The mutation settles after the first paint; without this every assertion reads the
  // "Checking…" state the screen renders while it is in flight.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  return container;
}

describe("VerifyEmailScreen with a token", () => {
  it("spends the token exactly once, even though StrictMode mounts it twice", async () => {
    const fetch = respondVerified();
    await mountVerifyEmail(TOKEN);

    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("posts the token from the address to the verify endpoint", async () => {
    const fetch = respondVerified();
    await mountVerifyEmail(TOKEN);

    const [url, init] = fetch.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe("/api/v1/auth/verify-email");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ token: TOKEN });
  });

  it("says the address is verified and offers the way onward", async () => {
    respondVerified();
    const container = await mountVerifyEmail(TOKEN);

    expect(container.textContent).toContain("Email verified");
    expect(container.querySelector('a[href="/traces"]')).not.toBeNull();
  });

  it("shows the API's refusal in the API's own words, without narrowing it", async () => {
    respondRefused();
    const container = await mountVerifyEmail(TOKEN);

    expect(container.textContent).toContain(LINK_NO_LONGER_VALID);
    // The screen must not invent a reason the API deliberately withheld.
    expect(container.textContent).not.toContain("expired link");
    expect(container.textContent).not.toContain("already verified");
    expect(container.querySelector('a[href="/login"]')).not.toBeNull();
  });
});

describe("VerifyEmailScreen with no token", () => {
  it("never calls the endpoint, because an empty token is not a request worth making", async () => {
    const fetch = respondVerified();
    await mountVerifyEmail(undefined);

    expect(fetch).not.toHaveBeenCalled();
  });

  it("says the link is incomplete rather than that it expired", async () => {
    respondVerified();
    const container = await mountVerifyEmail(undefined);

    expect(container.textContent).toContain("This link is incomplete");
    expect(container.textContent).not.toContain("no longer valid");
  });
});
