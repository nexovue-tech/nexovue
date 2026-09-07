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

import { ForgotPasswordScreen } from "./ForgotPasswordScreen";

/**
 * The defect these guard: the reset endpoint answers 202 for a known and an unknown
 * address alike, and that identical answer *is* the security property. A screen that
 * said "no account with that address" would hand the whole thing back — the form would
 * become a way to ask us which of a list of addresses are customers.
 */

const KNOWN = "raj@example.com";
const UNKNOWN = "nobody-at-all@example.com";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

/** 202 with a `null` body — what FastAPI renders for this endpoint, for every address. */
function respondAccepted() {
  const fetch = vi.fn(
    async () =>
      new Response("null", { status: 202, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

async function mountForgotPassword(): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const rootRoute = createRootRoute({
    component: () =>
      createElement(QueryClientProvider, { client }, createElement(ForgotPasswordScreen)),
  });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([loginRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));

  return container;
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

/** Fills the address in and submits, then lets the mutation settle. */
async function askForALink(container: HTMLElement, address: string) {
  const input = container.querySelector<HTMLInputElement>('input[name="email"]');
  const form = container.querySelector("form");
  if (!input || !form) throw new Error("the form is not on the screen");

  await act(async () => typeInto(input, address));
  // `requestSubmit` runs the real submit path; the form carries `noValidate`, as the
  // other auth forms do, so the browser's own validation does not intercept it.
  await act(async () => form.requestSubmit());
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("ForgotPasswordScreen", () => {
  it("answers a known and an unknown address with exactly the same screen", async () => {
    respondAccepted();

    const withKnown = await mountForgotPassword();
    await askForALink(withKnown, KNOWN);
    const known = withKnown.textContent;

    const withUnknown = await mountForgotPassword();
    await askForALink(withUnknown, UNKNOWN);

    expect(known).toBe(withUnknown.textContent);
    expect(known).toContain("If that address has an account");
  });

  it("does not put the address back on the screen, so nothing can vary with it", async () => {
    respondAccepted();
    const container = await mountForgotPassword();
    await askForALink(container, KNOWN);

    expect(container.textContent).not.toContain(KNOWN);
  });

  it("posts the address to the reset endpoint", async () => {
    const fetch = respondAccepted();
    const container = await mountForgotPassword();
    await askForALink(container, KNOWN);

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe("/api/v1/auth/password-reset");
    expect(JSON.parse(String(init.body))).toEqual({ email: KNOWN });
  });

  it("shows the form, not a confirmation, before anything has been asked for", async () => {
    respondAccepted();
    const container = await mountForgotPassword();

    expect(container.querySelector('input[name="email"]')).not.toBeNull();
    expect(container.textContent).not.toContain("Check your inbox");
  });
});
