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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openAssistant, resetAssistant } from "@/shared/assistant-context";
import { identityFor } from "@/shared/identity.test-util";
import type { FixedIntervalRefresh } from "@/shared/refresh";
import { declareStream, setPaused } from "@/shared/refresh";

import { AppShell } from "./layout/AppShell";

/**
 * A dashboard in full screen (F20, FR55, T6.9, AC76): the shell hides its sidebar and top
 * bar and keeps the age and the paused chip; "Leave full screen" is first in the tab order;
 * Escape leaves. Without `display=1` the shell is whole.
 */

let root: Root | undefined;

const declaration: FixedIntervalRefresh = {
  kind: "fixed-interval",
  fixedWindow: false,
  savedIntervalMs: 60_000,
  intervalMs: 60_000,
  tabIntervalMs: undefined,
  chooseInterval: () => {},
  lastGoodAt: "2026-11-11T02:14:00.000Z",
  zone: "UTC",
  reading: false,
  readNow: () => {},
  chooseRange: () => {},
};

async function mountAt(initial: string) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname.replace("/api/v1/", "");
      const body =
        path === "auth/me"
          ? identityFor("owner")
          : path === "projects"
            ? { projects: [] }
            : { connections: [], findings: [] };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  const rootRoute = createRootRoute({ component: AppShell });
  const view = createRoute({
    getParentRoute: () => rootRoute,
    path: "/dashboards/$dashboardId",
    component: () => createElement("p", null, "the dashboard"),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([view]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        createElement(RouterProvider, { router }),
      ),
    );
  });
  await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
  return { container, router };
}

// The chip says "showing 02:14" only on the reading's own day; pin the reader's day to it.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(Date.parse("2026-11-11T02:20:00.000Z"));
});

afterEach(() => {
  vi.useRealTimers();
  if (root) act(() => root?.unmount());
  root = undefined;
  document.body.replaceChildren();
  declareStream(undefined);
  setPaused(false);
  resetAssistant();
  vi.unstubAllGlobals();
});

describe("full screen", () => {
  it("keeps the sidebar and top bar without display=1", async () => {
    declareStream(declaration);
    const { container } = await mountAt("/dashboards/d1");
    expect(container.querySelector("nav, aside")).not.toBeNull();
    expect(container.textContent).toContain("Pick a range");
    expect(container.textContent).not.toContain("Leave full screen");
  });

  it("hides the sidebar and top bar, and keeps the age and the paused chip (AC76)", async () => {
    declareStream(declaration);
    setPaused(true);
    const { container } = await mountAt("/dashboards/d1?display=1");
    expect(container.querySelector("nav, aside")).toBeNull();
    expect(container.querySelector('[aria-label="Open navigation"]')).toBeNull();
    expect(container.textContent).toContain("Paused · showing 02:14");
    expect(container.textContent).toContain("the dashboard");
  });

  it("puts 'Leave full screen' first in the tab order, visible on focus (AC76)", async () => {
    declareStream(declaration);
    const { container } = await mountAt("/dashboards/d1?display=1");
    const first = container.querySelector("button");
    expect(first?.textContent).toBe("Leave full screen");
    expect(first?.className).toContain("sr-only");
    expect(first?.className).toContain("focus:not-sr-only");
  });

  it("starts the keyboard at the top, so one Tab reaches it after the button that opened it (AC76)", async () => {
    declareStream(declaration);
    const { container } = await mountAt("/dashboards/d1?display=1");
    // Without this, focus is lost to the page's start where the pressed button used to be, and
    // Tab begins in the middle of the dashboard (seen in Chromium, 2026-11-22).
    const bar = container.querySelector("header");
    expect(document.activeElement).toBe(bar);
    expect(bar?.contains(container.querySelector("button"))).toBe(true);
    expect(bar?.getAttribute("tabindex")).toBe("-1");
  });

  it("shows it when the pointer moves", async () => {
    declareStream(declaration);
    const { container } = await mountAt("/dashboards/d1?display=1");
    await act(async () => window.dispatchEvent(new Event("pointermove")));
    expect(container.querySelector("button")?.className).not.toContain("sr-only");
  });

  it("leaves on Escape and on the button, replacing the entry", async () => {
    declareStream(declaration);
    const { container, router } = await mountAt("/dashboards/d1?display=1&range=6h");
    const before = router.history.length;
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(router.history.location.search).not.toContain("display");
    expect(router.history.location.search).toContain("6h");
    expect(router.history.length).toBe(before);
    expect(container.textContent).toContain("Pick a range");
  });

  /**
   * F4's chat panel is not among what full screen keeps (FR55), so a panel left open before
   * full screen stays out of it, and nothing behind it is made inert: below `lg` an open
   * panel makes the screen behind it inert, which with the panel hidden would leave a wall
   * display no one can use. Leaving full screen brings the panel back as it was.
   */
  const chatPanel = (container: HTMLElement) =>
    container.querySelector('aside[aria-label="Ask about your telemetry"]');

  it("keeps chat's open panel out of full screen, and nothing inert behind it", async () => {
    declareStream(declaration);
    openAssistant();
    const { container } = await mountAt("/dashboards/d1?display=1");
    expect(chatPanel(container)).toBeNull();
    expect(container.querySelector("[inert]")).toBeNull();
    expect(container.textContent).toContain("Leave full screen");
  });

  it("brings chat's open panel back on leaving full screen", async () => {
    declareStream(declaration);
    openAssistant();
    const { container } = await mountAt("/dashboards/d1?display=1");
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(chatPanel(container)?.hasAttribute("hidden")).toBe(false);
  });
});
