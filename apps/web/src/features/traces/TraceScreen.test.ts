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

import { setSelectedProjectId } from "@/shared/selection";

import { TraceScreen } from "./TraceScreen";

/**
 * The defect these guard: nine in ten trace ids on a log line were sampled away, and
 * the screen rendered every one of them as a failure with a Retry button that could
 * never succeed. A 404 from this endpoint is an answer; anything else is a failure.
 */

const TRACE_ID = "89862496c280c749485ae30629e9737f";
const PROJECT_ID = "01a06b03-5939-7797-9b6c-b400f31e392e";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.scrollTo = () => {};
  setSelectedProjectId(PROJECT_ID);
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

/** The API's own shape: one handler emits RFC 9457 problem+json for every failure. */
function respondWith(status: number, title: string, detail: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ type: "urn:test", title, status, detail }), {
          status,
          headers: { "content-type": "application/problem+json" },
        }),
    ),
  );
}

/** The screen links back to `/traces`, so it is mounted inside a real router. */
async function mountTraceScreen(): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute({
    component: () =>
      createElement(
        QueryClientProvider,
        { client },
        createElement(TraceScreen, { traceId: TRACE_ID }),
      ),
  });
  const tracesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/traces",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([tracesRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));
  // The query resolves after the first paint; without this every assertion reads
  // the skeleton the screen renders while it is pending.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  return container;
}

function retryButton(container: HTMLElement): HTMLElement | null {
  return (
    Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "Try again",
    ) ?? null
  );
}

describe("TraceScreen when the store has no such trace", () => {
  it("explains sampling instead of reporting a failure", async () => {
    respondWith(404, "No such resource in this organization", "No such trace.");
    const container = await mountTraceScreen();

    expect(container.textContent).toContain("This trace was not kept");
    expect(container.textContent).toContain("sampled");
    expect(container.textContent).not.toContain("could not be loaded");
  });

  it("offers no retry, because the same request returns the same 404 for ever", async () => {
    respondWith(404, "No such resource in this organization", "No such trace.");
    const container = await mountTraceScreen();

    expect(retryButton(container)).toBeNull();
  });

  it("renders the same words whatever the 404 body says", async () => {
    // Tenant isolation depends on "sampled away" and "belongs to someone else" being
    // one answer. The screen branches on the status alone, so even a 404 whose body
    // differed could not show through: assert that, rather than trusting the wording.
    respondWith(404, "No such resource in this organization", "No such trace.");
    const sampledAway = (await mountTraceScreen()).textContent;

    respondWith(404, "Not found", "That trace belongs to another organization.");
    const anotherTenant = (await mountTraceScreen()).textContent;

    expect(sampledAway).toBe(anotherTenant);
    expect(sampledAway).toContain(TRACE_ID);
  });
});

describe("TraceScreen when the request actually failed", () => {
  it("reports a server error as a failure, with a retry", async () => {
    respondWith(500, "The database is unreachable", "Try again shortly.");
    const container = await mountTraceScreen();

    expect(container.textContent).toContain("This trace could not be loaded");
    expect(container.textContent).not.toContain("This trace was not kept");
    expect(retryButton(container)).not.toBeNull();
  });
});
