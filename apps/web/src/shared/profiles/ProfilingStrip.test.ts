// @vitest-environment jsdom

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
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import type { ProfiledService } from "./api";
import { ProfilingStrip } from "./ProfilingStrip";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

async function render(profiled: ProfiledService | undefined): Promise<HTMLElement> {
  const rootRoute = createRootRoute({
    component: () =>
      createElement(ProfilingStrip, {
        serviceName: "checkout",
        profiled,
        phrase: "in the last hour",
      }),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      createRoute({ getParentRoute: () => rootRoute, path: "/profiles", component: () => null }),
    ]),
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

describe("the service page's profiling strip (FR11)", () => {
  it("says a profiled service is, which kinds, when last, and opens its profile", async () => {
    const container = await render({
      service_name: "checkout",
      types: [
        { sample_type: "samples", unit: "count" },
        { sample_type: "inuse_space", unit: "bytes" },
        { sample_type: "inuse_objects", unit: "count" },
      ],
      last_received: new Date(Date.now() - 120_000).toISOString(),
    });
    const text = container.textContent ?? "";

    expect(text).toMatch(/Profiled — CPU, memory, last received .*ago\./);
    expect(container.querySelector('a[href="/profiles?service=checkout"]')?.textContent).toBe(
      "Open profile",
    );
    expect(text).toContain("Preview");
  });

  it("says a service with no profiles in the window has none, and how to set it up", async () => {
    const container = await render(undefined);

    expect(container.textContent).toContain("No profiles in the last hour.");
    expect(
      container.querySelector('a[href="/docs/profiling/continuous-profiling"]'),
    ).not.toBeNull();
  });
});
