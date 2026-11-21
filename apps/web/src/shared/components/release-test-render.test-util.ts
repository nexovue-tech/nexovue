import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import type { ReactElement } from "react";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";

/** A release component inside a router that knows the release route, for its links. */

const mounted: Root[] = [];

export async function renderWithRelease(element: () => ReactElement): Promise<HTMLElement> {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const rootRoute = createRootRoute({ component: element });
  const release = createRoute({
    getParentRoute: () => rootRoute,
    path: "/releases/$releaseId",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([release]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));
  return container;
}

export function unmountAll(): void {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
}
