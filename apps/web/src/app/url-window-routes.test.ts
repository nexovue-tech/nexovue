// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { identityFor } from "@/shared/identity.test-util";
import { SELECTION_STORAGE_KEY, setSelectedRangeId } from "@/shared/selection";

import { type AppRouter, createAppRouter } from "./router";

/**
 * A finding's window in the address, through the app's own router (F3 T2.3; AC21).
 * Each of the six screens a finding links to keeps `from`, `to`, `project` and `finding`
 * through its `validateSearch`; a filter change on a screen keeps them; the window line
 * says whose window it is; and the person's stored window is never written.
 */

const API_PREFIX = "/api/v1";
const SETTLE_ROUNDS = 60;
const FROM = "2026-11-09T13:47:00.000Z";
const TO = "2026-11-09T14:20:00.000Z";
const PROJECT = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0021";
const FINDING = "01a1c18a-3778-726a-ac51-855da271a38a";
const WINDOW = { from: FROM, to: TO, project: PROJECT, finding: FINDING };
const QUERY = new URLSearchParams(WINDOW).toString();

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  setSelectedRangeId("1h");
  localStorage.removeItem(SELECTION_STORAGE_KEY);
});

/** `/auth/me` answers; every other read is empty, which no window line depends on. */
function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname.slice(API_PREFIX.length);
      if (path === "/auth/me") {
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

async function mountAt(path: string): Promise<{ container: HTMLElement; router: AppRouter }> {
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
  return { container, router };
}

async function waitFor<T>(find: () => T | null | undefined | false, what: string): Promise<T> {
  for (let round = 0; round < SETTLE_ROUNDS; round += 1) {
    const found = find();
    if (found) return found;
    await settle();
  }
  throw new Error(`gave up waiting for ${what}`);
}

const addressWindow = (container: HTMLElement) =>
  container.querySelector<HTMLElement>('[data-testid="address-window"]');

/** The search the leaf route validated: what its screen reads, not the raw address. */
const searchOf = (router: AppRouter) =>
  (router.state.matches.at(-1)?.search ?? {}) as Record<string, unknown>;

describe("a finding's window in the address (AC21)", () => {
  it.each(["/traces", "/logs", "/errors", "/infrastructure", "/releases", "/services/checkout"])(
    "is read on %s, and kept in what its route validates",
    async (path) => {
      const { container, router } = await mountAt(`${path}?${QUERY}`);

      await waitFor(() => addressWindow(container), "the window line");
      expect(searchOf(router)).toMatchObject(WINDOW);
    },
  );

  it("names the finding's window, offers the way back and the person's own", async () => {
    const { container } = await mountAt(`/traces?${QUERY}`);
    const line = await waitFor(() => addressWindow(container), "the window line");

    expect(line.textContent).toContain("Showing this finding's window,");
    expect(line.className).toContain("flex-wrap");
    const back = [...line.querySelectorAll("a")].find(
      (a) => a.textContent === "Back to the finding",
    );
    expect(back?.getAttribute("href")).toBe(`/findings/${FINDING}`);
    expect(line.textContent).toContain("Use my own window");
  });

  it("with no finding, shows the plain window and no way back", async () => {
    const { container } = await mountAt(`/logs?from=${FROM}&to=${TO}`);
    const line = await waitFor(() => addressWindow(container), "the window line");

    expect(line.textContent).toMatch(/^Showing \d/);
    expect(line.textContent).not.toContain("Back to the finding");
  });

  it("keeps the window when a filter changes, and never writes the stored one", async () => {
    setSelectedRangeId("6h");
    const storedWindow = () =>
      JSON.parse(localStorage.getItem(SELECTION_STORAGE_KEY) ?? "{}").window;
    const before = storedWindow();
    const { container, router } = await mountAt(`/errors?${QUERY}`);
    const toggle = await waitFor(
      () => container.querySelector<HTMLInputElement>('main input[type="checkbox"]'),
      "the errors screen's filter toggle",
    );

    await act(async () => toggle.click());
    await settle();

    expect(searchOf(router)).toMatchObject({ ...WINDOW, new_only: "true" });
    expect(before).toEqual({ kind: "relative", rangeId: "6h" });
    expect(storedWindow()).toEqual(before);
  });

  it("ends with 'Use my own window', leaving the filters", async () => {
    const { container, router } = await mountAt(`/traces?${QUERY}&service=checkout`);
    const line = await waitFor(() => addressWindow(container), "the window line");
    const mine = [...line.querySelectorAll("button")].find(
      (button) => button.textContent === "Use my own window",
    );

    await act(async () => mine?.click());
    await settle();

    expect(searchOf(router)).toEqual({ service: "checkout" });
    expect(addressWindow(container)).toBeNull();
  });

  it("is not read on a screen a finding does not link to", async () => {
    const { container } = await mountAt(`/overview?${QUERY}`);
    await settle();

    expect(addressWindow(container)).toBeNull();
  });
});
