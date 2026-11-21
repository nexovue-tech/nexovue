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

import { identityFor } from "@/shared/identity.test-util";

import { SampleExplanationScreen } from "./SampleExplanationScreen";

/**
 * `/findings/sample` (F3 T2.13; FR46, AC84's sample case): labelled, from fixed data,
 * with no API call for any data, no action and no link into a system that is not there.
 */

const mounted: Root[] = [];
const fetched: string[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  fetched.length = 0;
});

async function mount(): Promise<HTMLElement> {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      fetched.push(url.pathname);
      return new Response(JSON.stringify(identityFor("member")), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute({ component: Outlet });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      createRoute({
        getParentRoute: () => rootRoute,
        path: "/findings/sample",
        component: SampleExplanationScreen,
      }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/findings/sample"] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
        createElement(RouterProvider, { router } as any),
      ),
    ),
  );
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  return container;
}

const header = (container: HTMLElement) =>
  container.querySelector('[data-testid="explanation-header"]')?.textContent ?? "";

describe("the sample explanation (FR46)", () => {
  it("is labelled a sample, names a likely place to look, and reads no data", async () => {
    const container = await mount();

    expect(container.textContent).toContain("Sample");
    expect(container.textContent).toContain("not a reading of your system");
    expect(header(container)).toContain("Likely: the deploy of checkout v2.1");
    expect(fetched.filter((path) => !path.endsWith("/auth/me"))).toEqual([]);
  });

  it("offers no action and no link into data that does not exist", async () => {
    const container = await mount();
    const section = container.querySelector('[aria-labelledby="finding-explanation"]');

    expect(section?.querySelector("a[href^='/traces'], a[href^='/errors']")).toBeNull();
    expect(section?.textContent).not.toContain("Read the facts again");
    expect(section?.textContent).not.toContain("Was this useful?");
  });

  it("shows an honest abstention and facts that could not be read", async () => {
    const container = await mount();
    const choose = container.querySelector("select") as HTMLSelectElement;

    await act(async () => {
      choose.value = "abstained";
      choose.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(header(container)).toBe("Not enough evidence to name a likely cause");

    await act(async () => {
      choose.value = "absences";
      choose.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(header(container)).toBe("Could not finish reading the facts, so no likely cause yet");
  });
});
