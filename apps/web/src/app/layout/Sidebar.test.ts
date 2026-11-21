// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { identityFor } from "@/shared/identity.test-util";

import { createAppRouter } from "../router";

/**
 * The sidebar in the real route tree (AC55). `/` is matched exactly
 * (`activeOptions`), so once the Findings feed moved there, nothing made its
 * entry active on a finding's own page — a reader on `/findings/f1` saw no
 * screen marked at all. This renders the app's own router, not a stand-in.
 */

const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

/** `/auth/me` answers; every other read fails, which no sidebar entry depends on. */
function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const path = url.pathname.slice(API_PREFIX.length);
      if ((init?.method ?? "GET") === "GET" && path === "/auth/me") {
        return new Response(JSON.stringify(identityFor("member")), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(
        JSON.stringify({ type: "about:blank", title: "Not found", status: 404 }),
        { status: 404, headers: { "content-type": "application/problem+json" } },
      );
    }),
  );
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function mountAt(path: string): Promise<HTMLElement> {
  stubApi();
  window.history.replaceState(null, "", path);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createAppRouter(client);
  await router.load();

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(
      createElement(QueryClientProvider, { client }, createElement(RouterProvider, { router })),
    ),
  );
  await settle();
  return container;
}

async function waitFor<T>(find: () => T | null | undefined | false, what: string): Promise<T> {
  for (let round = 0; round < SETTLE_ROUNDS; round += 1) {
    const found = find();
    if (found) return found;
    await settle();
  }
  throw new Error(`gave up waiting for ${what}`);
}

/** The sidebar entry whose visible text is `title`. */
function navLink(container: HTMLElement, title: string): HTMLAnchorElement | undefined {
  return [...container.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Screens"] a')].find(
    (link) => (link.textContent ?? "").trim() === title,
  );
}

const isActive = (link: HTMLAnchorElement | undefined) =>
  link?.getAttribute("aria-current") === "page";

describe("which sidebar entry is active (AC55)", () => {
  it("marks Findings on /, and not Overview", async () => {
    const container = await mountAt("/");
    const findings = await waitFor(() => navLink(container, "Findings"), "the Findings entry");

    expect(findings.getAttribute("href")).toBe("/");
    expect(isActive(findings)).toBe(true);
    expect(isActive(navLink(container, "Overview"))).toBe(false);
  });

  it("marks Overview on /overview, and not Findings", async () => {
    const container = await mountAt("/overview");
    const overview = await waitFor(() => navLink(container, "Overview"), "the Overview entry");

    expect(overview.getAttribute("href")).toBe("/overview");
    expect(isActive(overview)).toBe(true);
    expect(isActive(navLink(container, "Findings"))).toBe(false);
  });

  it("marks Findings on a finding's own page, where / no longer prefix-matches", async () => {
    const container = await mountAt("/findings/f1");
    const findings = await waitFor(() => navLink(container, "Findings"), "the Findings entry");

    expect(isActive(findings)).toBe(true);
    expect(isActive(navLink(container, "Overview"))).toBe(false);
  });
});

describe("the 404 screen (FR44)", () => {
  it('says "Back to Findings" and goes to /', async () => {
    const container = await mountAt("/no-such-screen");
    const back = await waitFor(
      () =>
        [...container.querySelectorAll<HTMLAnchorElement>("a")].find(
          (link) => (link.textContent ?? "").trim() === "Back to Findings",
        ),
      "the 404 screen's way back",
    );

    expect(back.getAttribute("href")).toBe("/");
  });
});
