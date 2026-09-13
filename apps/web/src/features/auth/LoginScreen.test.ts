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

import { LoginScreen } from "./LoginScreen";
import { parseLoginSearch } from "./search";

/**
 * The two-step sign-in (T64), and the defects it would have if the screen got it wrong.
 *
 * 1. **Treating the challenge answer as a session.** `POST /auth/login` answers with one
 *    of two bodies and sets no cookie on the challenge path. A screen that navigated
 *    inside on either answer would put somebody who has only proved a password onto a
 *    screen they have not earned — and then meet a 401 on the first request.
 * 2. **Narrowing the refusal.** Every way the second step can fail is one 401 with one
 *    sentence, so that somebody holding a stolen password cannot learn which half of
 *    their attack is landing. A screen that guessed at "wrong code" versus "expired"
 *    would invent the distinction the API refuses to make.
 */

const LOGIN_PATH = "/api/v1/auth/login";
const SECOND_FACTOR_PATH = "/api/v1/auth/login/second-factor";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const IDENTITY = {
  user: {
    id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0001",
    email: "nexovue@nexovue.com",
    name: "Dev",
    role: "owner",
    email_verified_at: "2026-09-01T09:15:00Z",
  },
  organization: { id: "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0002", slug: "dev", name: "Dev Co" },
};

const CHALLENGE = { second_factor_required: true, challenge: "a-short-lived-token" };

const REFUSAL = {
  type: "https://nexovue.com/problems/unauthenticated",
  title: "Not signed in",
  status: 401,
  detail: "That code was not accepted. Sign in again to start a new attempt.",
};

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": status === 401 ? "application/problem+json" : "application/json",
    },
  });

function stubApi(options: { secondFactor?: boolean; codeAccepted?: boolean } = {}) {
  const posted: string[] = [];

  const fetchMock = vi.fn(async (url: URL) => {
    if (url.pathname === LOGIN_PATH) {
      posted.push(url.pathname);
      return json(options.secondFactor ? CHALLENGE : IDENTITY, 200);
    }
    if (url.pathname === SECOND_FACTOR_PATH) {
      posted.push(url.pathname);
      return options.codeAccepted ? json(IDENTITY, 200) : json(REFUSAL, 401);
    }
    throw new Error(`no stub for ${url.pathname}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return { fetch: fetchMock, posted };
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mountLogin() {
  const container = document.createElement("div");
  document.body.append(container);

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  const rootRoute = createRootRoute();
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/login",
    component: () => createElement(QueryClientProvider, { client }, createElement(LoginScreen, {})),
    validateSearch: parseLoginSearch,
  });
  const tracesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/traces",
    component: () => createElement("p", null, "Traces"),
  });
  const signupRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/signup",
    component: () => createElement("p", null, "Sign up"),
  });
  const forgotRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/forgot-password",
    component: () => createElement("p", null, "Forgot"),
  });

  const router = createRouter({
    routeTree: rootRoute.addChildren([loginRoute, tracesRoute, signupRoute, forgotRoute]),
    history: createMemoryHistory({ initialEntries: ["/login"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();

  return { container, router };
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
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

async function submitCredentials(container: HTMLElement) {
  await act(async () => typeInto(field(container, "email"), "nexovue@nexovue.com"));
  await act(async () => typeInto(field(container, "password"), "correct-horse-battery"));
  const form = field(container, "password").closest("form");
  if (!form) throw new Error("the credentials are not in a form");
  await act(async () => form.requestSubmit());
  await settle();
}

async function submitCode(container: HTMLElement, code: string) {
  await act(async () => typeInto(field(container, "code"), code));
  const form = field(container, "code").closest("form");
  if (!form) throw new Error("the code field is not in a form");
  await act(async () => form.requestSubmit());
  await settle();
}

describe("signing in without a second factor", () => {
  it("lands inside on the one answer that carries a session", async () => {
    stubApi();
    const { container, router } = await mountLogin();

    await submitCredentials(container);

    expect(router.state.location.pathname).toBe("/traces");
  });
});

describe("signing in with a second factor", () => {
  it("asks for a code instead of going inside", async () => {
    stubApi({ secondFactor: true });
    const { container, router } = await mountLogin();

    await submitCredentials(container);

    expect(router.state.location.pathname).toBe("/login");
    expect(container.textContent).toContain("One more step");
    expect(container.querySelector('input[name="code"]')).not.toBeNull();
  });

  it("offers the recovery codes as the way out for somebody without their phone", async () => {
    stubApi({ secondFactor: true });
    const { container } = await mountLogin();

    await submitCredentials(container);

    expect(container.textContent).toContain("recovery codes");
  });

  it("finishes the sign-in when the code is accepted", async () => {
    const api = stubApi({ secondFactor: true, codeAccepted: true });
    const { container, router } = await mountLogin();

    await submitCredentials(container);
    await submitCode(container, "123456");

    expect(api.posted).toEqual([LOGIN_PATH, SECOND_FACTOR_PATH]);
    expect(router.state.location.pathname).toBe("/traces");
  });

  it("passes the API's one refusal through rather than inventing a reason", async () => {
    stubApi({ secondFactor: true });
    const { container, router } = await mountLogin();

    await submitCredentials(container);
    await submitCode(container, "000000");

    expect(container.textContent).toContain("That code was not accepted");
    expect(container.textContent).not.toContain("expired");
    expect(router.state.location.pathname).toBe("/login");
  });

  it("sends nothing when the code field is empty", async () => {
    const api = stubApi({ secondFactor: true });
    const { container } = await mountLogin();

    await submitCredentials(container);
    await submitCode(container, "   ");

    expect(api.posted).toEqual([LOGIN_PATH]);
  });

  it("can go back to the credentials, because a spent challenge needs them again", async () => {
    stubApi({ secondFactor: true });
    const { container } = await mountLogin();

    await submitCredentials(container);
    const startAgain = [...container.querySelectorAll("button")].find(
      (candidate) => candidate.textContent?.trim() === "Start again",
    );
    if (!startAgain) throw new Error("there is no way back to the credentials");
    await act(async () => startAgain.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await settle();

    expect(container.querySelector('input[name="email"]')).not.toBeNull();
  });
});
