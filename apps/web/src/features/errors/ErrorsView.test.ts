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

import type { ErrorGroupDetail, ErrorGroupSummary, ErrorOccurrence } from "./api";
import { ErrorsView } from "./ErrorsScreen";

const DECLINED: ErrorGroupSummary = {
  group_id: "82fe32c42683a275",
  exception_type: "AuthorizationDeclined",
  message_template: "card authorization declined by upstream",
  latest_message: "card authorization declined by upstream",
  grouped_by: "type_and_message_template",
  occurrences: 158,
  first_seen: "2026-08-28T19:58:31.447Z",
  started_in_window: true,
  last_seen: "2026-08-28T20:11:00.411Z",
  services: ["demo-payments"],
  sample_trace_id: "89862496c280c749485ae30629e9737f",
  sample_span_id: "c8f8acc1e53ceb86",
  sample_source: "log",
};

/** Running since before the window opened: a long-standing bug, not an incident. */
const LONG_STANDING: ErrorGroupSummary = {
  ...DECLINED,
  group_id: "0f1e2d3c4b5a6978",
  exception_type: "TimeoutError",
  message_template: "read timed out after <n>s",
  latest_message: "read timed out after 30s",
  occurrences: 12_483,
  first_seen: "2026-08-28T15:00:00.050Z",
  started_in_window: false,
  services: ["demo-checkout", "demo-storefront", "demo-payments"],
};

const STACK = [
  'File "/app/payments.py", line 88, in authorize',
  "    raise AuthorizationDeclined(response.reason)",
  "AuthorizationDeclined: card authorization declined by upstream",
].join("\n");

function occurrence(overrides: Partial<ErrorOccurrence> = {}): ErrorOccurrence {
  return {
    source: "log",
    service_name: "demo-payments",
    occurred_at: "2026-08-28T20:11:00.411Z",
    trace_id: "89862496c280c749485ae30629e9737f",
    span_id: "c8f8acc1e53ceb86",
    // A Python service that stamped no version, which is the common case and
    // the one where symbolication cannot run at all.
    release: "",
    exception_message: "card authorization declined by upstream",
    stacktrace: "",
    ...overrides,
  };
}

const VIEWPORT_HEIGHT = 900;
const VIEWPORT_WIDTH = 1400;

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

  // The router scrolls to the top on its first match, and jsdom has no scrolling.
  window.scrollTo = () => {};

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
  groups?: readonly ErrorGroupSummary[];
  trendByGroup?: ReadonlyMap<string, readonly { bucket: string; occurrences: number }[]>;
  isPending?: boolean;
  isError?: boolean;
  error?: unknown;
  selected?: ErrorGroupSummary;
  detail?: ErrorGroupDetail;
  detailIsPending?: boolean;
  detailIsError?: boolean;
  detailError?: unknown;
}

/**
 * The panel links into a trace, so the view is mounted inside a real router. A memory
 * history rather than the browser's: the test asserts on what is rendered, and jsdom
 * has no address bar worth driving.
 */
async function mountInRouter(element: ReturnType<typeof createElement>): Promise<HTMLElement> {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const rootRoute = createRootRoute({ component: () => element });
  const traceRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/traces/$traceId",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([traceRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  // The router resolves its first match asynchronously; without this the provider
  // renders its pending state and every assertion below reads an empty container.
  await router.load();

  const root = createRoot(container);
  mounted.push(root);
  await act(async () => root.render(createElement(RouterProvider, { router })));

  return container;
}

function render(overrides: Overrides = {}): Promise<HTMLElement> {
  return mountInRouter(
    createElement(ErrorsView, {
      filters: {},
      onFilterChange: () => {},
      onClearFilters: () => {},
      windowLabel: "6 hours",
      // Nothing symbolicated: these fixtures are a Python service, which has no
      // release and no source map, and the stack is shown as it arrived.
      symbolication: { frames: undefined, isPending: false, isError: false },
      groups: [DECLINED, LONG_STANDING],
      isPending: false,
      isError: false,
      onRetry: () => {},
      onSelect: () => {},
      detailIsPending: false,
      detailIsError: false,
      onDetailRetry: () => {},
      ...overrides,
    }),
  );
}

const readable = (container: HTMLElement) =>
  (container.textContent ?? "").replace(/\s+/g, " ").trim();

describe("ErrorsView", () => {
  it("waits with a skeleton, never a spinner", async () => {
    const container = await render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading errors"]')).not.toBeNull();
    expect(container.innerHTML).toContain("animate-pulse");
    expect(readable(container)).not.toContain("AuthorizationDeclined");
  });

  it("shows a failed load inline, with the correlation id and a retry", async () => {
    const error = new ApiError("boom", 503, {
      traceId: "abc",
      correlationId: "corr-42",
      offline: false,
      aborted: false,
    });
    const rendered = readable(await render({ isError: true, error }));

    expect(rendered).toContain("Errors could not be loaded");
    expect(rendered).toContain("corr-42");
    expect(rendered).toContain("Try again");
  });

  it("says what to do about an empty window rather than showing a blank area", async () => {
    const rendered = readable(await render({ groups: [] }));

    expect(rendered).toContain("Nothing threw in this window");
    expect(rendered).toContain("widen the time range");
  });

  it("makes the grouping the headline: distinct exceptions and what they collapsed", async () => {
    const rendered = readable(await render());

    expect(rendered).toContain("2 distinct exceptions · 12,641 occurrences in the last 6 hours");
    // One line per exception, not one per crash.
    expect(rendered).toContain("AuthorizationDeclined");
    expect(rendered).toContain("158");
    expect(rendered).toContain("12,483");
  });

  it("separates an incident from a long-standing bug in words, not only in colour", async () => {
    const container = await render();
    const rendered = readable(container);

    expect(rendered).toContain("before window");
    // The group that began inside the window is the only one wearing the accent rule.
    expect(container.querySelectorAll(".border-l-critical-solid")).toHaveLength(1);
    expect(container.querySelectorAll(".border-l-transparent")).toHaveLength(1);
  });

  it("collapses a long service list rather than letting it push the columns", async () => {
    expect(readable(await render())).toContain("demo-checkout +2");
  });

  it("opens one group beside the list without losing the list", async () => {
    const detail: ErrorGroupDetail = {
      group: DECLINED,
      recent: [occurrence(), occurrence({ stacktrace: STACK, service_name: "demo-payments" })],
    };
    const container = await render({ selected: DECLINED, detail });
    const rendered = readable(container);

    expect(container.querySelector('[aria-label="Error detail"]')).not.toBeNull();
    expect(rendered).toContain("TimeoutError"); // the list is still there
    expect(rendered).toContain("Recent instances (2)");
    expect(rendered).toContain("exception type and message");
  });

  it("shows the stack trace in a box that scrolls instead of one that grows", async () => {
    const detail: ErrorGroupDetail = {
      group: DECLINED,
      // Newest first, and the newest carried no stack — the older one does.
      recent: [occurrence(), occurrence({ stacktrace: STACK })],
    };
    const stack = (await render({ selected: DECLINED, detail })).querySelector("pre");

    // Every frame is there, each its own line so a long one wraps rather than
    // demanding a horizontal scroll inside a 26rem panel.
    expect([...(stack?.children ?? [])].map((line) => line.textContent)).toEqual(STACK.split("\n"));
    expect(stack?.className).toContain("overflow-y-auto");
    expect(stack?.className).toContain("max-h-80");
  });

  it("says so plainly when no instance carried a stack trace", async () => {
    const detail: ErrorGroupDetail = { group: DECLINED, recent: [occurrence()] };
    const container = await render({ selected: DECLINED, detail });

    expect(readable(container)).toContain("None of the recorded instances carried one.");
    expect(container.querySelector("pre")).toBeNull();
  });

  it("offers the trace behind an instance, and says when there is none to offer", async () => {
    const detail: ErrorGroupDetail = {
      group: DECLINED,
      recent: [occurrence(), occurrence({ trace_id: "", span_id: "" })],
    };
    const container = await render({ selected: DECLINED, detail });

    expect(container.querySelector(`a[href="/traces/${DECLINED.sample_trace_id}"]`)).not.toBeNull();
    expect(readable(container)).toContain("no trace");
  });

  it("keeps the list usable when only the detail read failed", async () => {
    const rendered = readable(
      await render({ selected: DECLINED, detailIsError: true, detailError: new Error("nope") }),
    );

    expect(rendered).toContain("Instances could not be loaded");
    expect(rendered).toContain("TimeoutError");
  });
});

describe("ErrorsView trends", () => {
  const trend = (peak: number, count = 6) =>
    Array.from({ length: count }, (_, index) => ({
      bucket: new Date(index * 60_000).toISOString(),
      occurrences: Math.round((peak / count) * (index + 1)),
    }));

  const endHeights = (container: HTMLElement) =>
    [...container.querySelectorAll("polyline")].map((line) => {
      const ys = (line.getAttribute("points") ?? "")
        .split(" ")
        .filter(Boolean)
        .map((pair) => Number(pair.split(",")[1]));
      return ys[ys.length - 1];
    });

  it("draws every group against one scale, so two rows can be compared", async () => {
    const container = await render({
      groups: [LONG_STANDING, DECLINED],
      trendByGroup: new Map([
        [LONG_STANDING.group_id, trend(100)],
        [DECLINED.group_id, trend(10)],
      ]),
    });

    const [busy, quiet] = endHeights(container);

    // Lower y is higher on screen: the busier group must end visibly above the quieter.
    expect(busy).toBeLessThan(quiet);
  });

  it("keeps a group whose trend never arrived, and rules the cell", async () => {
    const container = await render({
      groups: [LONG_STANDING, DECLINED],
      trendByGroup: new Map([[LONG_STANDING.group_id, trend(100)]]),
    });

    expect(container.querySelectorAll("polyline")).toHaveLength(1);
    expect(readable(container)).toContain(DECLINED.exception_type);
  });
});
