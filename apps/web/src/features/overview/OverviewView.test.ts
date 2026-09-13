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

import type { OverviewViewProps } from "./OverviewScreen";
import { OverviewView } from "./OverviewScreen";
import { buildOverviewPlots } from "./overview-plots";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

const TOTALS = {
  service_count: 11,
  request_count: 504_000,
  error_count: 6_048,
  requests_per_minute: 8_400,
  error_rate: 0.012,
  p50_ms: 42,
  p95_ms: 210,
  p99_ms: 980,
};

const POINTS = [
  {
    bucket: "2026-09-13T10:00:00Z",
    requests_per_minute: 8_000,
    error_rate: 0.01,
    p50_ms: 40,
    p95_ms: 200,
    p99_ms: 900,
  },
  {
    bucket: "2026-09-13T10:01:00Z",
    requests_per_minute: 8_800,
    error_rate: 0.014,
    p50_ms: 44,
    p95_ms: 220,
    p99_ms: 1_060,
  },
];

const BASE: OverviewViewProps = {
  windowLabel: "hour",
  totals: TOTALS,
  plots: buildOverviewPlots(POINTS),
  markers: [],
  worstServices: [
    {
      service_name: "checkout-api",
      requests_per_minute: 1_200,
      error_rate: 0.041,
      error_count: 49,
      p95_ms: 340,
    },
    {
      service_name: "catalog",
      requests_per_minute: 900,
      error_rate: 0,
      error_count: 0,
      p95_ms: 18,
    },
  ],
  isPending: false,
  isError: false,
  onRetry: () => {},
  surfaces: {
    nodes: { total: 6, worst: 1 },
    checks: { total: 3, worst: 1 },
    lcp: { p75: 2_800, readings: 400 },
  },
};

async function render(overrides: Partial<OverviewViewProps> = {}): Promise<HTMLElement> {
  const rootRoute = createRootRoute({
    component: () => createElement(OverviewView, { ...BASE, ...overrides }),
  });
  const children = ["/services", "/services/$name", "/infrastructure", "/synthetics", "/rum"].map(
    (path) => createRoute({ getParentRoute: () => rootRoute, path, component: () => null }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(children),
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

const readable = async (overrides: Partial<OverviewViewProps> = {}) =>
  ((await render(overrides)).textContent ?? "").replace(/\s+/g, " ").trim();

describe("the six states", () => {
  it("waits with a skeleton, never a spinner", async () => {
    const container = await render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading the overview"]')).not.toBeNull();
  });

  it("shows a failed load inline, with a retry", async () => {
    const text = await readable({ isError: true, error: new Error("boom") });

    expect(text).toContain("The overview could not be loaded");
  });

  it("says nothing has reported rather than showing a screen of zeroes", async () => {
    // A dashboard of zeroes is what an outage looks like. `service_count` of
    // zero is the honest signal: nothing reported, rather than something
    // reporting nothing.
    const text = await readable({ totals: { ...TOTALS, service_count: 0 } });

    expect(text).toContain("Nothing has reported yet");
    expect(text).not.toContain("8,400");
  });

  it("leads with the four numbers a person reads first", async () => {
    const text = await readable();

    expect(text).toContain("11");
    expect(text).toContain("210ms");
  });
});

describe("the surface strip", () => {
  it("gives each surface a line and a way in", async () => {
    const container = await render();
    const destinations = [...container.querySelectorAll("a")].map((link) =>
      link.getAttribute("href"),
    );

    expect(destinations).toContain("/infrastructure");
    expect(destinations).toContain("/synthetics");
    expect(destinations).toContain("/rum");
  });

  it("names what is wrong on a surface beside what is on it", async () => {
    const text = await readable();

    expect(text).toContain("6 nodes");
    expect(text).toContain("1 stale");
    expect(text).toContain("1 down");
  });

  it("shows an em dash for a surface that has sent nothing, never a zero", async () => {
    // "0 nodes" and "no collector configured" are different findings, and only
    // one of them is a problem with the fleet.
    const text = await readable({ surfaces: {} });

    expect(text).toContain("—");
    expect(text).not.toContain("0 nodes");
  });

  it("shows an em dash when the read answered with an empty fleet", async () => {
    // Found in the browser: the strip read "INFRASTRUCTURE 0 nodes". The test
    // above passes an *absent* surface, which is a different case — here the
    // read succeeded and returned nothing, and zero is not a reading. Nothing
    // has scraped a machine yet; a fleet of none is a claim we cannot make.
    const text = await readable({
      surfaces: { nodes: { total: 0, worst: 0 }, checks: { total: 0, worst: 0 } },
    });

    expect(text).not.toContain("0 nodes");
    expect(text).not.toContain("0 checks");
    expect(text).toContain("—");
  });

  it("shows no browser reading when nothing was measured", async () => {
    // A p75 of four readings is not a percentile, and the RUM screen already
    // refuses to show one. The strip must not undo that.
    const text = await readable({
      surfaces: { ...BASE.surfaces, lcp: { p75: 2_800, readings: 0 } },
    });

    expect(text).not.toContain("LCP 2.8s");
  });
});

describe("the deploy rules", () => {
  it("says once that the rules are deploys, for all three panels", async () => {
    // One sentence rather than a legend per panel: the rule is at the same
    // instant on each, and saying it three times would imply three readings.
    const text = await readable({
      markers: [{ at: Date.parse("2026-09-13T10:00:30Z") / 1000, version: "v9" }],
    });

    expect(text).toContain("1 deploy in this window");
  });

  it("says nothing about deploys when there were none", async () => {
    expect(await readable()).not.toContain("deploy");
  });
});
