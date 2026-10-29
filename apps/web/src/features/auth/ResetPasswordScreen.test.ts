// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { LoginScreen } from "./LoginScreen";
import { ResetPasswordScreen } from "./ResetPasswordScreen";
import { parseLoginSearch } from "./search";

/**
 * The two things that matter here. **A reset must not sign anybody in** — the API issues
 * no session on purpose, and a screen that worked around that would hold a session the
 * reset had just invalidated. And **the customer must be told the other sessions are
 * gone**, because that is the surprising half of what just happened to their account.
 *
 * The whole hop is exercised: the real sign-in screen is mounted at `/login` behind the
 * real search parser, so the notice has to survive the address bar to appear.
 */

const TOKEN = "5FhqYlF0aRDG3Iw8nXCyKZ4Vb2tMuPQeS7jJdRvNzUk";
const NEW_PASSWORD = "a-long-enough-password";

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

/** 204: the endpoint sets the password and answers with no body and no session. */
function respondReset() {
  const fetch = vi.fn(async () => new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

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

interface Mounted {
  container: HTMLElement;
  location: () => { pathname: string; search: Record<string, unknown> };
}

async function mountResetPassword(token: string | undefined): Promise<Mounted> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  // The root renders an outlet, not the screen: the reset navigates to `/login`, and a
  // root that rendered the screen itself would keep showing it after the hop.
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const rootRoute = createRootRoute({
    component: () => createElement(QueryClientProvider, { client }, createElement(Outlet)),
  });
  const resetRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => createElement(ResetPasswordScreen, { token }),
  });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    validateSearch: parseLoginSearch,
    component: () => createElement(LoginScreen, loginRoute.useSearch()),
  });
  const forgotRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/forgot-password",
    component: () => null,
  });
  const signupRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/signup",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([resetRoute, loginRoute, forgotRoute, signupRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));

  return {
    container,
    location: () => ({
      pathname: router.state.location.pathname,
      search: router.state.location.search as Record<string, unknown>,
    }),
  };
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function submitNewPassword(container: HTMLElement, password: string) {
  const input = container.querySelector<HTMLInputElement>('input[name="password"]');
  const form = container.querySelector("form");
  if (!input || !form) throw new Error("the form is not on the screen");

  await act(async () => typeInto(input, password));
  await act(async () => form.requestSubmit());
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("ResetPasswordScreen with a token", () => {
  it("posts the token and the new password to the confirm endpoint", async () => {
    const fetch = respondReset();
    const { container } = await mountResetPassword(TOKEN);
    await submitNewPassword(container, NEW_PASSWORD);

    // Only the reset posts; the sign-in screen it lands on asks its own questions.
    const posts = (fetch.mock.calls as unknown as [URL, RequestInit][]).filter(
      ([, init]) => init?.method === "POST",
    );
    expect(posts).toHaveLength(1);
    const [url, init] = posts[0];
    expect(url.pathname).toBe("/api/v1/auth/password-reset/confirm");
    expect(JSON.parse(String(init.body))).toEqual({ token: TOKEN, password: NEW_PASSWORD });
  });

  it("sends the customer to sign in rather than into the app, because no session was issued", async () => {
    respondReset();
    const { container, location } = await mountResetPassword(TOKEN);
    await submitNewPassword(container, NEW_PASSWORD);

    expect(location().pathname).toBe("/login");
    expect(location().search).toEqual({ notice: "password-reset" });
  });

  it("tells them the password changed and every other session is signed out", async () => {
    respondReset();
    const { container } = await mountResetPassword(TOKEN);
    await submitNewPassword(container, NEW_PASSWORD);

    expect(container.textContent).toContain("Your password is changed");
    expect(container.textContent).toContain("signed out");
  });

  it("shows the API's refusal and stays put when the link is spent", async () => {
    respondRefused();
    const { container, location } = await mountResetPassword(TOKEN);
    await submitNewPassword(container, NEW_PASSWORD);

    expect(container.textContent).toContain(LINK_NO_LONGER_VALID);
    expect(location().pathname).toBe("/");
  });
});

describe("ResetPasswordScreen with no token", () => {
  it("offers a fresh link instead of a form there is nothing to submit with", async () => {
    const fetch = respondReset();
    const { container } = await mountResetPassword(undefined);

    expect(container.querySelector("form")).toBeNull();
    expect(container.textContent).toContain("This link is incomplete");
    expect(container.querySelector('a[href="/forgot-password"]')).not.toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
});
