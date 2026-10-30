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

import { ApiError } from "@/shared/api-client";
import { UNKNOWN } from "@/shared/format/number";

import type { EndpointGroup, EndpointHealth, ServiceHealth, TrendPoint } from "./api";
import { ServicesView } from "./ServicesScreen";

/** The busiest tenant's worst row, as the API returns it. */
const STOREFRONT: ServiceHealth = {
  service_name: "demo-storefront",
  request_count: 2867,
  error_count: 158,
  requests_per_minute: 47.8,
  error_rate: 158 / 2867,
  p50_ms: 12.4,
  p95_ms: 62.9,
  p99_ms: 88.1,
};

const HEALTHY: ServiceHealth = {
  service_name: "demo-catalog",
  request_count: 900,
  error_count: 0,
  requests_per_minute: 15,
  error_rate: 0,
  p50_ms: 4.2,
  p95_ms: 11.5,
  p99_ms: 19,
};

const CHECKOUT: EndpointHealth = {
  service_name: "demo-storefront",
  span_kind: "SERVER",
  endpoint: "POST /checkout",
  request_count: 2867,
  error_count: 158,
  requests_per_minute: 47.8,
  error_rate: 158 / 2867,
  p50_ms: 12.4,
  p95_ms: 62.9,
  p99_ms: 88.1,
};

const VIEWPORT_HEIGHT = 800;
const VIEWPORT_WIDTH = 1200;

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

  // jsdom does no layout, so the scroll container measures zero tall and the
  // virtualizer correctly renders nothing into a viewport that does not exist.
  for (const [property, size] of [
    ["clientHeight", VIEWPORT_HEIGHT],
    ["clientWidth", VIEWPORT_WIDTH],
  ] as const) {
    Object.defineProperty(HTMLElement.prototype, property, { configurable: true, value: size });
  }
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

interface Overrides {
  order?: "worst" | "slowest";
  windowLabel?: string;
  services?: readonly ServiceHealth[];
  isPending?: boolean;
  isError?: boolean;
  error?: unknown;
  expanded?: ReadonlySet<string>;
  endpointsByService?: ReadonlyMap<string, EndpointGroup>;
  trendByService?: ReadonlyMap<string, readonly TrendPoint[]>;
  previousByService?: ReadonlyMap<string, ServiceHealth>;
  everReported?: boolean;
}

/** `count` buckets climbing to `peak` on each series, which is all a sparkline reads. */
function series(peak: number, { errorPeak = 0, count = 6 } = {}): readonly TrendPoint[] {
  return Array.from({ length: count }, (_, index) => ({
    bucket: new Date(index * 60_000).toISOString(),
    requests_per_minute: (peak / count) * (index + 1),
    error_rate: (errorPeak / count) * (index + 1),
    p95_ms: 10,
  }));
}

/** Two services that are both healthy, so both rows plot the same series. */
const QUIET: ServiceHealth = { ...HEALTHY, service_name: "demo-quiet" };

async function render(overrides: Overrides = {}): Promise<HTMLElement> {
  // The rows link to `/services/$name`, so `Link` needs a router to resolve against.
  const rootRoute = createRootRoute({
    component: () =>
      createElement(ServicesView, {
        order: "worst",
        onOrderChange: () => {},
        filters: {},
        onFilterChange: () => {},
        onClearFilters: () => {},
        windowLabel: "the last hour",
        services: [STOREFRONT, HEALTHY],
        isPending: false,
        isError: false,
        onRetry: () => {},
        expanded: new Set<string>(),
        onToggleService: () => {},
        endpointsByService: new Map<string, EndpointGroup>(),
        everReported: true,
        ...overrides,
      }),
  });
  const children = ["/services/$name"].map((path) =>
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

const readable = (container: HTMLElement) =>
  (container.textContent ?? "").replace(/\s+/g, " ").trim();

function endpointsLoaded(endpoints: EndpointHealth[]): ReadonlyMap<string, EndpointGroup> {
  return new Map([["demo-storefront", { status: "success", endpoints, refetch: () => {} }]]);
}

describe("ServicesView", () => {
  it("waits with a skeleton, never a spinner", async () => {
    const container = await render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading services"]')).not.toBeNull();
    expect(container.innerHTML).toContain("animate-pulse");
    expect(readable(container)).not.toContain("demo-storefront");
  });

  it("shows a failed load inline, with the correlation id and a retry", async () => {
    const error = new ApiError("boom", 503, {
      traceId: "abc",
      correlationId: "corr-42",
      offline: false,
      aborted: false,
    });
    const rendered = readable(await render({ isError: true, error }));

    expect(rendered).toContain("Services could not be loaded");
    expect(rendered).toContain("corr-42");
    expect(rendered).toContain("Try again");
  });

  it("says what to do about an empty window rather than showing a blank area", async () => {
    const rendered = readable(await render({ services: [], windowLabel: "the last hour" }));

    expect(rendered).toContain("No services in the last hour");
    expect(rendered).toContain("Widen the time range");
  });

  it("keeps the API's ranking and puts the numbers in aligned columns", async () => {
    const container = await render();
    const rendered = readable(container);

    expect(rendered).toContain("2 services reporting in the last hour, worst first.");
    expect(rendered.indexOf("demo-storefront")).toBeLessThan(rendered.indexOf("demo-catalog"));
    // 47.8/min reads as 48: a tenth of a request a minute is not a fact anyone acts on.
    expect(rendered).toContain("48");
    expect(rendered).toContain("158 · 5.5%");
    expect(rendered).toContain("62.9ms");
    expect(container.innerHTML).toContain('title="2,867 requests in this window"');
  });

  it("re-ranks by the slow tail when the toggle asks for it", async () => {
    // The server re-ranks, so what arrives is already in the asked-for order and
    // the view renders it as served. What this holds is that the caption says
    // which ranking is in force — a list ordered one way and captioned the other
    // is worse than no caption.
    const rendered = readable(await render({ order: "slowest", services: [HEALTHY, STOREFRONT] }));

    expect(rendered).toContain("slowest first.");
    expect(rendered.indexOf("demo-catalog")).toBeLessThan(rendered.indexOf("demo-storefront"));
  });

  it("colours the errors and leaves a healthy service uncoloured", async () => {
    const container = await render();

    expect(container.innerHTML).toContain("text-critical-text");
    // Health is the absence of colour: a rule, not a zero to read past.
    expect(readable(container)).toContain("—");
  });

  it("opens a service's endpoints underneath it", async () => {
    const container = await render({
      expanded: new Set(["demo-storefront"]),
      endpointsByService: endpointsLoaded([CHECKOUT]),
    });
    const rendered = readable(container);

    expect(rendered).toContain("POST /checkout");
    expect(rendered).toContain("SERVER");
    expect(container.querySelector('[aria-expanded="true"]')).not.toBeNull();
  });

  it("opens the service itself, not only its endpoints", async () => {
    // The list of services was the one screen that did not link to a service: the whole
    // row was the expand toggle, so the detail page was reachable only from Overview and
    // the service map.
    const container = await render();

    expect(container.querySelector('a[href="/services/demo-storefront"]')).not.toBeNull();
    // And the chevron stays a toggle — opening the endpoints must not leave the list.
    expect(container.querySelector("[aria-expanded]")?.closest("a")).toBeNull();
  });

  it("distinguishes a service with no endpoints from one still loading them", async () => {
    expect(
      readable(
        await render({
          expanded: new Set(["demo-storefront"]),
          endpointsByService: endpointsLoaded([]),
        }),
      ),
    ).toContain("No endpoints reported in this window.");

    const loading = await render({ expanded: new Set(["demo-storefront"]) });
    expect(loading.querySelector('[aria-label="Loading endpoints"]')).not.toBeNull();
  });

  it("offers a retry when only the endpoints failed", async () => {
    const rendered = readable(
      await render({
        expanded: new Set(["demo-storefront"]),
        endpointsByService: new Map([
          ["demo-storefront", { status: "error", endpoints: [], refetch: () => {} }],
        ]),
      }),
    );

    expect(rendered).toContain("Endpoints could not be loaded.");
    expect(rendered).toContain("Try again");
    // One failed sub-read does not take the rest of the screen with it.
    expect(rendered).toContain("demo-catalog");
  });

  it("offers the slowest-first escape hatch as a pressed control", async () => {
    const container = await render();
    const pressed = [...container.querySelectorAll("[aria-pressed]")].map((button) => [
      button.textContent,
      button.getAttribute("aria-pressed"),
    ]);

    expect(pressed).toEqual([
      ["worst", "true"],
      ["slowest", "false"],
    ]);
  });
});

/** The y coordinates of one row's sparkline, in document order. */
function sparklineHeights(container: HTMLElement, index: number): number[] {
  const polylines = [...container.querySelectorAll("polyline")];
  return (polylines[index]?.getAttribute("points") ?? "")
    .split(" ")
    .filter(Boolean)
    .map((pair) => Number(pair.split(",")[1]));
}

describe("ServicesView trends", () => {
  it("draws rows of the same kind against one scale, so two can be compared", async () => {
    const container = await render({
      services: [HEALTHY, QUIET],
      trendByService: new Map([
        ["demo-catalog", series(100)],
        ["demo-quiet", series(10)],
      ]),
    });

    const busy = sparklineHeights(container, 0);
    const quiet = sparklineHeights(container, 1);

    // Lower y is higher on screen. Under one shared scale the busier service must draw
    // visibly taller; under per-row scaling both would peak at the same height and the
    // column would be unreadable across rows.
    expect(busy[busy.length - 1]).toBeLessThan(quiet[quiet.length - 1]);
  });

  it("plots what the colour claims: a failing row draws its errors, not its traffic", async () => {
    // Traffic falling while errors climb. If the row plotted traffic it would slope down;
    // a red line that slopes down while a service is breaking is a lie the colour tells.
    const falling = [...series(60, { errorPeak: 0.4 })].reverse().map((point, index, all) => ({
      ...point,
      error_rate: all[all.length - 1 - index].error_rate,
    }));
    const container = await render({
      services: [STOREFRONT],
      trendByService: new Map([["demo-storefront", falling]]),
    });

    const drawn = sparklineHeights(container, 0);

    expect(container.innerHTML).toContain("stroke-critical-solid");
    expect(drawn[drawn.length - 1]).toBeLessThan(drawn[0]);
    expect(container.querySelector('svg[role="img"]')?.getAttribute("aria-label")).toContain(
      "error rate",
    );
  });

  it("says which series a healthy row is showing", async () => {
    const container = await render({
      services: [HEALTHY],
      trendByService: new Map([["demo-catalog", series(100)]]),
    });

    expect(container.querySelector('svg[role="img"]')?.getAttribute("aria-label")).toContain(
      "request rate",
    );
    expect(container.innerHTML).toContain("stroke-series-1");
  });

  it("keeps a row whose trend never arrived, and rules the cell", async () => {
    const container = await render({
      services: [HEALTHY, QUIET],
      trendByService: new Map([["demo-catalog", series(100)]]),
    });

    expect(readable(container)).toContain("demo-quiet");
    // One sparkline drawn, one cell ruled: a missing series must not remove the row.
    expect(container.querySelectorAll("polyline")).toHaveLength(1);
    expect(readable(container)).toContain(UNKNOWN);
  });

  it("renders rows before any trend has arrived", async () => {
    const container = await render({ trendByService: undefined });

    expect(readable(container)).toContain("demo-storefront");
    expect(container.querySelectorAll("polyline")).toHaveLength(0);
  });
});

describe("ServicesView severity", () => {
  it("marks a failing service and leaves a healthy one unmarked", async () => {
    const container = await render();
    const marks = container.querySelectorAll('[data-severity="critical"]');

    // Health is the absence of a mark: two services, one failing, one mark.
    expect(marks).toHaveLength(1);
  });

  it("marks nothing at all when nothing is failing", async () => {
    const container = await render({ services: [HEALTHY] });

    expect(container.querySelectorAll("[data-severity]")).toHaveLength(0);
  });
});

describe("ServicesView empty states", () => {
  it("offers setup only when the project has never reported", async () => {
    const container = await render({ services: [], everReported: false });
    const text = readable(container);

    expect(text).toContain("No services yet");
    expect(text).toContain("OpenTelemetry SDK");
  });

  it("offers a wider range when a project that has reported is quiet now", async () => {
    const container = await render({
      services: [],
      everReported: true,
      windowLabel: "the last 15 minutes",
    });
    const text = readable(container);

    // A customer with eleven services must never be told they have none.
    expect(text).toContain("No services in the last 15 minutes");
    expect(text).toContain("Widen the time range");
    expect(text).not.toContain("No services yet");
  });

  it("never offers setup on a project silent for longer than any range reaches", async () => {
    // The whole point of replacing the old widest-window guess: eight days of
    // silence is not a project that was never instrumented.
    const container = await render({
      services: [],
      everReported: true,
      windowLabel: "the last 7 days",
    });

    expect(readable(container)).not.toContain("No services yet");
  });

  it("does not claim never while the project list has not answered", async () => {
    const container = await render({ services: [], everReported: undefined });
    const text = readable(container);

    expect(text).not.toContain("No services yet");
    expect(text).toContain("Widen the time range");
  });
});

/**
 * A delta always carries its sign; the error cell prints a bare percentage, so
 * "%" on its own finds the reading rather than the annotation.
 */
const SIGNED_CHANGE = /[+\u2212][\d.]+(?:%|pp)/;

describe("the change against the previous window", () => {
  /** The same service, with whatever the earlier window measured. */
  const earlier = (overrides: Partial<ServiceHealth>): ReadonlyMap<string, ServiceHealth> =>
    new Map([["demo-storefront", { ...STOREFRONT, ...overrides }]]);

  it("renders no delta when the previous window holds nothing for this service", async () => {
    // A service that first reported inside this window has nothing to compare
    // against, and "0%" would be a claim that it was flat.
    const rendered = readable(
      await render({ services: [STOREFRONT], previousByService: new Map() }),
    );

    expect(rendered).not.toMatch(SIGNED_CHANGE);
  });

  it("renders no delta when the reading is unremarkable", async () => {
    // Traffic up a tenth is movement, not news. Annotating it would spend the
    // reader's attention on a row that did not need it — and the row that
    // doubled would look exactly the same.
    const rendered = readable(
      await render({
        services: [STOREFRONT],
        previousByService: earlier({
          requests_per_minute: STOREFRONT.requests_per_minute / 1.3,
          p95_ms: STOREFRONT.p95_ms / 1.05,
          error_rate: STOREFRONT.error_rate - 0.001,
        }),
      }),
    );

    expect(rendered).not.toMatch(SIGNED_CHANGE);
  });

  it("names a change worth an eye, in the measure's own terms", async () => {
    const rendered = readable(
      await render({
        services: [STOREFRONT],
        previousByService: earlier({
          requests_per_minute: STOREFRONT.requests_per_minute / 2,
          p95_ms: STOREFRONT.p95_ms * 2,
          // 5.5% to 0.5%: five points, which is what a ratio is measured in.
          error_rate: STOREFRONT.error_rate + 0.05,
        }),
      }),
    );

    expect(rendered).toContain("+100%");
    // A true minus sign, not a hyphen: these sit in a column of numbers.
    expect(rendered).toContain("−" + "50%");
    expect(rendered).toContain("−" + "5.0pp");
  });

  it("leaves the endpoints under an open service unannotated", async () => {
    // The previous window's endpoints are not read, so a delta there would have
    // nothing behind it. Reserved columns keep them aligned with the row above.
    const rendered = readable(
      await render({
        services: [STOREFRONT],
        expanded: new Set(["demo-storefront"]),
        endpointsByService: endpointsLoaded([CHECKOUT]),
        previousByService: earlier({ requests_per_minute: STOREFRONT.requests_per_minute / 2 }),
      }),
    );

    expect(rendered).toContain("POST /checkout");
    // One delta, on the service row, and none on the endpoint beneath it.
    expect(rendered.match(/\+100%/g) ?? []).toHaveLength(1);
  });
});
