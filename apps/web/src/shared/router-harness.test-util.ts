import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

import { SELECTION_STORAGE_KEY } from "@/shared/selection";

/**
 * A screen mounted in a real router and query client, with the project chosen, for tests
 * that replace only the network. Shared, so features' tests need not import each other.
 */

export const PROJECT_ID = "6f1f0a52-7d64-4c87-9b7a-0f0d1d1d1d01";
const SETTLE_ROUNDS = 40;

const mounted: Root[] = [];

export async function settle(rounds = SETTLE_ROUNDS): Promise<void> {
  for (let round = 0; round < rounds; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

/** Mounts `content` at `/`, inside `layout` when given (the shell), with the other screens
 *  a link may open, and the project chosen. */
export async function mount(
  content: () => ReactNode,
  layout: () => ReactNode = () => createElement(Outlet),
): Promise<HTMLElement> {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.localStorage.setItem(SELECTION_STORAGE_KEY, JSON.stringify({ projectId: PROJECT_ID }));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute({ component: layout });
  const screen = (path: string, component: () => ReactNode) =>
    createRoute({ getParentRoute: () => rootRoute, path, component });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      screen("/", content),
      screen("/services", () => createElement("p", null, "Services screen")),
      screen("/logs", () => createElement("p", null, "Logs screen")),
      screen("/findings/$findingId", () => null),
      screen("/settings/ai", () => null),
    ]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        // biome-ignore lint/suspicious/noExplicitAny: the router's props are generic over a tree this test builds
        createElement(RouterProvider, { router } as any),
      ),
    );
  });
  await settle();
  return container;
}

export function unmountAll(): void {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  window.localStorage.clear();
}
