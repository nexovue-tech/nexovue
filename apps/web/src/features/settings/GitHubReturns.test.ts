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
import { rememberLinkReturn } from "@/shared/github-link-return";
import { identityFor } from "@/shared/identity.test-util";

import { GitHubLinkReturnScreen } from "./GitHubLinkReturnScreen";
import { FixAppReturnScreen } from "./GitHubReturnScreen";
import { linkOutcome } from "./link-words";

/**
 * F5 (status decision 34; ADR-082): the two console pages GitHub sends a person back to.
 * The Fix App's return posts GitHub's `code` and `state` to the Fix App's own route and
 * lands on settings; the account link's return posts them, follows the attempt, and puts
 * the person back on the finding they came from. Only the network is replaced.
 */

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const problem = (status: number, detail: string, reason: string) =>
  new Response(JSON.stringify({ type: "about:blank", title: "x", status, detail, reason }), {
    status,
    headers: { "content-type": "application/problem+json" },
  });

interface Sent {
  method: string;
  path: string;
  body: unknown;
}

function stubApi(answer: (path: string, method: string) => Response | undefined): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const path = url.pathname.replace("/api/v1/", "");
      const method = init?.method ?? "GET";
      sent.push({
        method,
        path,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      const special = answer(path, method);
      if (special) return special;
      if (path === "auth/me") return json(identityFor("member"));
      if (path === "system/info") return json({ deployment: "self-hosted" });
      throw new Error(`no stub for ${method} ${url.pathname}`);
    }),
  );
  return sent;
}

async function settle() {
  for (let round = 0; round < 12; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(Screen: () => unknown, path: string, at: string) {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const landing = (where: string, words: string) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path: where,
      component: () => createElement("p", null, words),
    });
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path,
    // biome-ignore lint/suspicious/noExplicitAny: each screen is a plain component.
    component: () => createElement(QueryClientProvider, { client }, createElement(Screen as any)),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      screen,
      landing("/settings/connections", "settings landing"),
      landing("/findings/$findingId", "finding landing"),
      landing("/settings/account", "account landing"),
    ]),
    history: createMemoryHistory({ initialEntries: [at] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => {
    root.render(createElement(RouterProvider, { router }));
  });
  await settle();
  return { container, router };
}

function anchor(container: Element, words: string): HTMLAnchorElement | undefined {
  return [...container.querySelectorAll("a")].find((each) => each.textContent === words);
}

describe("the Fix App's return (decision 34)", () => {
  it("posts the code and state to the Fix App's own route, then lands on settings", async () => {
    const sent = stubApi((path) =>
      path === "connections/github/fix-app/manifest/complete"
        ? json({ connection_id: "c-1" }, 202)
        : undefined,
    );
    const { router } = await mount(
      FixAppReturnScreen,
      "/settings/connections/fix-app/return",
      "/settings/connections/fix-app/return?code=0123&state=s1",
    );

    const posted = sent.find(
      (each) => each.path === "connections/github/fix-app/manifest/complete",
    );
    expect(posted?.body).toEqual({ code: "0123", state: "s1" });
    expect(router.state.location.pathname).toBe("/settings/connections");
  });

  it("an expired setup link is refused in words, with Start again", async () => {
    const words = "This setup link has expired. Nothing was changed.";
    stubApi((path) =>
      path === "connections/github/fix-app/manifest/complete"
        ? problem(422, words, "expired")
        : undefined,
    );
    const { container } = await mount(
      FixAppReturnScreen,
      "/settings/connections/fix-app/return",
      "/settings/connections/fix-app/return?code=c&state=s",
    );

    expect(container.textContent).toContain(words);
    expect(anchor(container, "Start again")?.getAttribute("href")).toBe("/settings/connections");
  });
});

describe("the account link's return (decision 34)", () => {
  it("posts the code and state, then puts the person back on their finding", async () => {
    rememberLinkReturn("/findings/f-1", "a-1");
    const sent = stubApi((path) =>
      path === "connections/github/user-link/complete"
        ? json({ id: "a-1", status: "linked", reason: null }, 202)
        : undefined,
    );
    const { router } = await mount(
      GitHubLinkReturnScreen,
      "/settings/github-link/return",
      "/settings/github-link/return?code=c0de&state=s2",
    );

    const posted = sent.find((each) => each.path === "connections/github/user-link/complete");
    expect(posted?.body).toEqual({ code: "c0de", state: "s2" });
    expect(router.state.location.pathname).toBe("/findings/f-1");
  });

  it("a refusal is said in words, with a way back, and nothing remembered stays", async () => {
    rememberLinkReturn("/findings/f-1", "a-1");
    stubApi((path) =>
      path === "connections/github/user-link/complete"
        ? json({ id: "a-1", status: "refused", reason: "refused_by_github" }, 202)
        : undefined,
    );
    const { container } = await mount(
      GitHubLinkReturnScreen,
      "/settings/github-link/return",
      "/settings/github-link/return?code=c&state=s",
    );

    expect(container.textContent).toContain("GitHub refused the link. Nothing was changed.");
    expect(anchor(container, "Go back")?.getAttribute("href")).toBe("/findings/f-1");
    expect(sessionStorage.length).toBe(0);
  });

  it("a link not meant for this person shows the API's own sentence", async () => {
    stubApi((path) =>
      path === "connections/github/user-link/complete"
        ? problem(422, "This link is not valid. Nothing was changed.", "invalid")
        : undefined,
    );
    const { container } = await mount(
      GitHubLinkReturnScreen,
      "/settings/github-link/return",
      "/settings/github-link/return?code=c&state=s",
    );

    expect(container.textContent).toContain("This link is not valid. Nothing was changed.");
    expect(anchor(container, "Go back")?.getAttribute("href")).toBe("/settings/account");
  });

  it("the attempt is followed for a bounded time, then said not to have finished", () => {
    expect(linkOutcome("running", null, false)).toBeNull();
    expect(linkOutcome("running", null, true)).toBe(
      "GitHub has not answered yet. Go back and try again.",
    );
    expect(linkOutcome("failed", "expired", false)).toContain("This link has expired.");
    expect(linkOutcome("linked", null, false)).toBe("linked");
  });
});
