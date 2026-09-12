// @vitest-environment jsdom

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
    ["offsetHeight", VIEWPORT_HEIGHT],
    ["offsetWidth", VIEWPORT_WIDTH],
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
  widestWindow?: boolean;
}

/** `count` buckets climbing to `peak`, which is all a sparkline reads. */
function series(peak: number, count = 6): readonly TrendPoint[] {
  return Array.from({ length: count }, (_, index) => ({
    bucket: new Date(index * 60_000).toISOString(),
    requests_per_minute: (peak / count) * (index + 1),
    error_rate: 0,
    p95_ms: 10,
  }));
}

function render(overrides: Overrides = {}): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const root = createRoot(container);
  mounted.push(root);

  act(() =>
    root.render(
      createElement(ServicesView, {
        order: "worst",
        onOrderChange: () => {},
        windowLabel: "hour",
        services: [STOREFRONT, HEALTHY],
        isPending: false,
        isError: false,
        onRetry: () => {},
        expanded: new Set<string>(),
        onToggleService: () => {},
        endpointsByService: new Map<string, EndpointGroup>(),
        widestWindow: false,
        ...overrides,
      }),
    ),
  );

  return container;
}

const readable = (container: HTMLElement) =>
  (container.textContent ?? "").replace(/\s+/g, " ").trim();

function endpointsLoaded(endpoints: EndpointHealth[]): ReadonlyMap<string, EndpointGroup> {
  return new Map([["demo-storefront", { status: "success", endpoints, refetch: () => {} }]]);
}

describe("ServicesView", () => {
  it("waits with a skeleton, never a spinner", () => {
    const container = render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading services"]')).not.toBeNull();
    expect(container.innerHTML).toContain("animate-pulse");
    expect(readable(container)).not.toContain("demo-storefront");
  });

  it("shows a failed load inline, with the correlation id and a retry", () => {
    const error = new ApiError("boom", 503, {
      traceId: "abc",
      correlationId: "corr-42",
      offline: false,
      aborted: false,
    });
    const rendered = readable(render({ isError: true, error }));

    expect(rendered).toContain("Services could not be loaded");
    expect(rendered).toContain("corr-42");
    expect(rendered).toContain("Try again");
  });

  it("says what to do about an empty window rather than showing a blank area", () => {
    const rendered = readable(render({ services: [], windowLabel: "hour" }));

    expect(rendered).toContain("No services in the last hour");
    expect(rendered).toContain("Widen the time range");
  });

  it("keeps the API's ranking and puts the numbers in aligned columns", () => {
    const container = render();
    const rendered = readable(container);

    expect(rendered).toContain("2 services reporting in the last hour, worst first.");
    expect(rendered.indexOf("demo-storefront")).toBeLessThan(rendered.indexOf("demo-catalog"));
    // 47.8/min reads as 48: a tenth of a request a minute is not a fact anyone acts on.
    expect(rendered).toContain("48");
    expect(rendered).toContain("158 · 5.5%");
    expect(rendered).toContain("62.9ms");
    expect(container.innerHTML).toContain('title="2,867 requests in this window"');
  });

  it("re-ranks by the slow tail when the toggle asks for it", () => {
    const rendered = readable(render({ order: "slowest", services: [HEALTHY, STOREFRONT] }));

    expect(rendered).toContain("slowest first.");
    expect(rendered.indexOf("demo-storefront")).toBeLessThan(rendered.indexOf("demo-catalog"));
  });

  it("colours the errors and leaves a healthy service uncoloured", () => {
    const container = render();

    expect(container.innerHTML).toContain("text-critical-text");
    // Health is the absence of colour: a rule, not a zero to read past.
    expect(readable(container)).toContain("—");
  });

  it("opens a service's endpoints underneath it", () => {
    const container = render({
      expanded: new Set(["demo-storefront"]),
      endpointsByService: endpointsLoaded([CHECKOUT]),
    });
    const rendered = readable(container);

    expect(rendered).toContain("POST /checkout");
    expect(rendered).toContain("SERVER");
    expect(container.querySelector('[aria-expanded="true"]')).not.toBeNull();
  });

  it("distinguishes a service with no endpoints from one still loading them", () => {
    expect(
      readable(
        render({ expanded: new Set(["demo-storefront"]), endpointsByService: endpointsLoaded([]) }),
      ),
    ).toContain("No endpoints reported in this window.");

    const loading = render({ expanded: new Set(["demo-storefront"]) });
    expect(loading.querySelector('[aria-label="Loading endpoints"]')).not.toBeNull();
  });

  it("offers a retry when only the endpoints failed", () => {
    const rendered = readable(
      render({
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

  it("offers the slowest-first escape hatch as a pressed control", () => {
    const container = render();
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
  it("draws every row against one scale, so two rows can be compared", () => {
    const container = render({
      trendByService: new Map([
        ["demo-storefront", series(100)],
        ["demo-catalog", series(10)],
      ]),
    });

    const busy = sparklineHeights(container, 0);
    const quiet = sparklineHeights(container, 1);

    // Lower y is higher on screen. Under one shared scale the busier service must draw
    // visibly taller; under per-row scaling both would peak at the same height and the
    // column would be unreadable across rows.
    expect(busy[busy.length - 1]).toBeLessThan(quiet[quiet.length - 1]);
  });

  it("keeps a row whose trend never arrived, and rules the cell", () => {
    const container = render({ trendByService: new Map([["demo-storefront", series(100)]]) });

    expect(readable(container)).toContain("demo-catalog");
    // One sparkline drawn, one cell ruled: a missing series must not remove the row.
    expect(container.querySelectorAll("polyline")).toHaveLength(1);
    expect(readable(container)).toContain(UNKNOWN);
  });

  it("renders rows before any trend has arrived", () => {
    const container = render({ trendByService: undefined });

    expect(readable(container)).toContain("demo-storefront");
    expect(container.querySelectorAll("polyline")).toHaveLength(0);
  });
});

describe("ServicesView severity", () => {
  it("marks a failing service and leaves a healthy one unmarked", () => {
    const container = render();
    const marks = container.querySelectorAll('[data-severity="critical"]');

    // Health is the absence of a mark: two services, one failing, one mark.
    expect(marks).toHaveLength(1);
  });

  it("marks nothing at all when nothing is failing", () => {
    const container = render({ services: [HEALTHY] });

    expect(container.querySelectorAll("[data-severity]")).toHaveLength(0);
  });
});

describe("ServicesView empty states", () => {
  it("offers setup when the widest window is still empty", () => {
    const container = render({ services: [], widestWindow: true });
    const text = readable(container);

    expect(text).toContain("No services yet");
    expect(text).toContain("OpenTelemetry SDK");
  });

  it("offers a wider range when a narrow window is empty", () => {
    const container = render({ services: [], widestWindow: false, windowLabel: "15 minutes" });
    const text = readable(container);

    // A customer with eleven services must never be told they have none.
    expect(text).toContain("No services in the last 15 minutes");
    expect(text).toContain("Widen the time range");
    expect(text).not.toContain("No services yet");
  });
});
