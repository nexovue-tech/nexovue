// @vitest-environment jsdom

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api-client";

import type { EndpointGroup, EndpointHealth, ServiceHealth } from "./api";
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
  services?: readonly ServiceHealth[];
  isPending?: boolean;
  isError?: boolean;
  error?: unknown;
  expanded?: ReadonlySet<string>;
  endpointsByService?: ReadonlyMap<string, EndpointGroup>;
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
    const rendered = readable(render({ services: [] }));

    expect(rendered).toContain("No services in this window");
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
