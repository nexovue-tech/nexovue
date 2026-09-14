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

import type { HistogramLayout, HistogramResponse, MetricSummary } from "./api";
import { METRIC_NAME_KEY, metricFilters, metricsNarrowed } from "./filters";
import type { MetricsViewProps } from "./MetricsScreen";
import { MetricsView } from "./MetricsScreen";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

const HISTOGRAM_METRIC: MetricSummary = {
  metric_name: "http.server.duration",
  shapes: ["histogram"],
  unit: "ms",
  series: 12,
  points: 4_200,
  last_seen: "2026-09-13T10:00:00Z",
  readable: true,
};

/**
 * A shape with no reader. Listed with its counts and told plainly that it cannot
 * be drawn, rather than hidden — it *is* in the project, and a customer paying to
 * store it should learn that before learning we cannot chart it.
 */
const GAUGE_METRIC: MetricSummary = {
  metric_name: "queue.depth",
  shapes: ["gauge"],
  unit: "1",
  series: 3,
  points: 900,
  last_seen: "2026-09-13T10:00:00Z",
  readable: false,
};

const CONFLICTED_METRIC: MetricSummary = {
  metric_name: "db.client.operation.duration",
  shapes: ["histogram", "summary"],
  unit: "s",
  series: 5,
  points: 1_500,
  last_seen: "2026-09-13T10:00:00Z",
  readable: false,
};

const LAYOUT: HistogramLayout = {
  bounds: [1, 5, 25, 100],
  bucket_counts: [10, 40, 30, 15, 5],
  count: 100,
  sum: 1_800,
  min: 0.4,
  max: 420,
  services: ["checkout-api"],
  series: 4,
  p50: 12,
  p75: 30,
  p90: 70,
  p95: 92,
  p99: 100,
};

const MERGED: HistogramResponse = {
  metric_name: "http.server.duration",
  unit: "ms",
  description: "How long the server took",
  window_start: "2026-09-13T09:00:00Z",
  window_end: "2026-09-13T10:00:00Z",
  layouts: [LAYOUT],
  merged: true,
};

const BASE: MetricsViewProps = {
  windowLabel: "the last hour",
  metrics: [HISTOGRAM_METRIC, GAUGE_METRIC, CONFLICTED_METRIC],
  truncated: false,
  isPending: false,
  isError: false,
  onRetry: () => {},
  onSelect: () => {},
  filters: {},
  typedName: "",
  onTypedName: () => {},
  onFilterChange: () => {},
  onClearFilters: () => {},
  histogramPending: false,
  histogramError: false,
};

async function render(overrides: Partial<MetricsViewProps> = {}): Promise<HTMLElement> {
  const rootRoute = createRootRoute({
    component: () => createElement(MetricsView, { ...BASE, ...overrides }),
  });
  const children = ["/metrics"].map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path, component: () => null }),
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

const readable = async (overrides: Partial<MetricsViewProps> = {}) =>
  ((await render(overrides)).textContent ?? "").replace(/\s+/g, " ").trim();

describe("the six states", () => {
  it("waits with a skeleton, never a spinner", async () => {
    const container = await render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading the metric catalogue"]')).not.toBeNull();
  });

  it("shows a failed load inline, with a retry", async () => {
    const text = await readable({ isError: true, error: new Error("boom") });

    expect(text).toContain("The metric catalogue could not be loaded");
  });

  it("explains an empty window rather than showing an empty table", async () => {
    // Metrics are exported separately from traces, so a project can be tracing
    // happily and reporting no metrics at all. The copy says that, because
    // otherwise this screen reads as broken.
    const text = await readable({ metrics: [] });

    expect(text).toContain("No metrics in this window");
    expect(text).toContain("separately from traces");
  });

  it("lists every metric with its counts when nothing is selected", async () => {
    const text = await readable();

    expect(text).toContain("http.server.duration");
    expect(text).toContain("queue.depth");
    expect(text).toContain("4,200");
    // Nothing is selected, so no distribution is claimed.
    expect(text).not.toContain("Percentiles are estimates");
  });

  it("draws the distribution for a selected metric", async () => {
    const text = await readable({ selected: "http.server.duration", histogram: MERGED });

    expect(text).toContain("How long the server took");
    expect(text).toContain("Observations");
    expect(text).toContain("100");
  });

  it("says a selected metric reported nothing rather than drawing an empty chart", async () => {
    const text = await readable({
      selected: "http.server.duration",
      histogram: { ...MERGED, layouts: [] },
    });

    expect(text).toContain("reported nothing in this window");
  });
});

describe("what a metric we cannot draw is told", () => {
  it("lists an unreadable shape with its counts instead of hiding it", async () => {
    const text = await readable();

    expect(text).toContain("queue.depth");
    expect(text).toContain("900");
    expect(text).toContain("no chart yet");
  });

  it("does not offer a chart for a shape that has no reader", async () => {
    const container = await render();
    const rows = [...container.querySelectorAll("button")].filter((button) =>
      (button.textContent ?? "").includes("queue.depth"),
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].disabled).toBe(true);
  });

  it("marks a name that arrived in more than one shape", async () => {
    // Not an error: nothing stops a service sending one name as two shapes. It
    // is a misconfiguration worth seeing, so both are named.
    const text = await readable();

    expect(text).toContain("histogram, summary");
  });
});

describe("percentiles are labelled as the estimates they are", () => {
  it("says so beside the numbers, not in a tooltip", async () => {
    const text = await readable({ selected: "http.server.duration", histogram: MERGED });

    expect(text).toContain("Percentiles are estimates, interpolated from bucket boundaries");
  });

  it("shows all five estimates in order", async () => {
    const text = await readable({ selected: "http.server.duration", histogram: MERGED });

    for (const label of ["p50", "p75", "p90", "p95", "p99"]) expect(text).toContain(label);
  });

  it("renders an absent estimate as an em dash and never as zero", async () => {
    // Zero is a latency somebody would act on. Absence is not.
    const text = await readable({
      selected: "http.server.duration",
      histogram: {
        ...MERGED,
        layouts: [{ ...LAYOUT, count: 0, sum: null, p50: null, p95: null, p99: null }],
      },
    });

    expect(text).toContain("—");
  });
});

describe("two bucket layouts are never drawn as one", () => {
  const CONFLICT: HistogramResponse = {
    ...MERGED,
    metric_name: "db.client.operation.duration",
    unit: "s",
    merged: false,
    layouts: [
      {
        ...LAYOUT,
        bounds: [0, 5, 10],
        bucket_counts: [0, 100, 20, 3],
        services: ["product-catalog"],
      },
      {
        ...LAYOUT,
        bounds: [0.001, 0.005, 0.01],
        bucket_counts: [6, 93, 49, 29],
        services: ["accounting"],
      },
    ],
  };

  it("says nothing was merged, and why", async () => {
    const text = await readable({ selected: "db.client.operation.duration", histogram: CONFLICT });

    expect(text).toContain("different bucket layouts");
    expect(text).toContain("different scales");
  });

  it("names the services that disagree, which is the part somebody can fix", async () => {
    const text = await readable({ selected: "db.client.operation.duration", histogram: CONFLICT });

    expect(text).toContain("product-catalog");
    expect(text).toContain("accounting");
  });

  it("draws one chart per layout rather than one chart for both", async () => {
    const container = await render({
      selected: "db.client.operation.duration",
      histogram: CONFLICT,
    });

    expect(container.querySelectorAll('svg[role="img"]')).toHaveLength(2);
  });
});

describe("what the URL holds", () => {
  it("reads all three filters back out of a shared link", async () => {
    expect(metricFilters({ name: "queue", shape: "gauge", readable_only: "true" })).toEqual({
      name: "queue",
      shape: "gauge",
      readable_only: "true",
    });
  });

  it("treats a blank name as not filtering", async () => {
    // `?name=` reads as a filter in the address bar and is not one.
    expect(metricFilters({ name: "   " })).toEqual({});
  });

  it("ignores the chosen metric, which is a selection and not a filter", async () => {
    expect(metricFilters({ metric: "http.server.duration" })).toEqual({});
  });

  it("counts a typed name as narrowing, which the declared filters alone do not", async () => {
    expect(metricsNarrowed({})).toBe(false);
    expect(metricsNarrowed({ [METRIC_NAME_KEY]: "queue" })).toBe(true);
    expect(metricsNarrowed({ readable_only: "true" })).toBe(true);
  });
});

describe("asking the catalogue a question", () => {
  it("offers the five shapes the API accepts, spelled its way", async () => {
    const container = await render();
    const shapes = [...(container.querySelector("select")?.options ?? [])].map(
      (option) => option.value,
    );

    expect(shapes).toEqual(["", "gauge", "sum", "histogram", "exponential histogram", "summary"]);
  });

  it("reports every keystroke, including the one that empties the box", async () => {
    const typed: string[] = [];
    const container = await render({
      typedName: "queue",
      onTypedName: (value) => typed.push(value),
    });

    type(searchBox(container), "queue.depth");
    type(searchBox(container), "");

    expect(typed).toEqual(["queue.depth", ""]);
  });

  it("shows the applied filter in the box, so a shared link explains itself", async () => {
    const container = await render({
      filters: { [METRIC_NAME_KEY]: "queue" },
      typedName: "queue",
    });

    expect(searchBox(container).value).toBe("queue");
  });

  it("says a filter matched nothing rather than that nothing ever arrived", async () => {
    // Opposite actions: one asks the reader to drop a filter, the other to go
    // and instrument a service that is already instrumented.
    const text = await readable({ metrics: [], filters: { shape: "gauge" } });

    expect(text).toContain("No metrics match these filters");
    expect(text).not.toContain("separately from traces");
  });

  it("counts a name still settling as a filter, not as a project with no metrics", async () => {
    // The name reaches the URL a debounce after it is typed. Until it does,
    // `filters` holds nothing and only the box knows a filter is applied.
    const text = await readable({ metrics: [], filters: {}, typedName: "zzz" });

    expect(text).toContain("No metrics match these filters");
  });

  it("offers the way out of a filter that matched nothing", async () => {
    let cleared = 0;
    const container = await render({
      metrics: [],
      filters: { shape: "gauge" },
      onClearFilters: () => {
        cleared += 1;
      },
    });

    const clear = [...container.querySelectorAll("button")].find((button) =>
      (button.textContent ?? "").includes("Clear all filters"),
    );
    act(() => clear?.click());

    expect(cleared).toBe(1);
  });

  it("still explains an empty window when nothing was asked of it", async () => {
    const text = await readable({ metrics: [] });

    expect(text).toContain("No metrics in this window");
  });
});

function searchBox(container: HTMLElement): HTMLInputElement {
  const found = container.querySelector<HTMLInputElement>('input[type="search"]');
  if (!found) throw new Error("no search box rendered");
  return found;
}

/**
 * React tracks an input's value and drops an event whose value it believes it
 * already has, so a plain assignment here would report the first keystroke and
 * silently swallow the one that clears the box.
 */
function type(input: HTMLInputElement, value: string): void {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  act(() => {
    setValue?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
