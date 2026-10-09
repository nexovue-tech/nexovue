// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { SELECTION_STORAGE_KEY, setSelectedProjectId } from "@/shared/selection";

import type { Span } from "./api";
import { SpanDetail } from "./SpanDetail";
import type { WaterfallRow } from "./trace-tree";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

const TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";

beforeEach(() => {
  setSelectedProjectId("5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0099");
  // The profile check never answers here: these tests are about the rest of the pane.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

function withQueries(element: ReactElement): ReactElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, element);
}

const SPAN: Span = {
  span_id: "00f067aa0ba902b7",
  parent_span_id: "",
  service_name: "payments",
  span_name: "POST /charge",
  span_kind: "SERVER",
  start_time: "2026-09-13T10:00:00.000Z",
  duration_ms: 412.7,
  status_code: "STATUS_CODE_ERROR",
  status_message: "handler returned 500",
  http_method: "POST",
  http_route: "/charge",
  http_status: 500,
  attributes: { tier: "gold" },
  is_model_call: false,
};

function render(span: Partial<Span>): string {
  const row: WaterfallRow = {
    key: "row-1",
    span: { ...SPAN, ...span },
    depth: 0,
    childCount: 0,
    orphaned: false,
    precedesParent: false,
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);
  act(() =>
    root.render(
      withQueries(createElement(SpanDetail, { traceId: TRACE_ID, row, onClose: () => {} })),
    ),
  );
  return (container.textContent ?? "").replace(/\s+/g, " ").trim();
}

describe("what the span threw", () => {
  it("leads the pane, above everything else about the span", () => {
    // The type and the message are the two things a person opened this span to
    // read. Below the timing and the HTTP detail they are found last.
    const text = render({ exception_type: "CardDeclined", exception_message: "issuer said no" });

    expect(text).toContain("CardDeclined");
    expect(text).toContain("issuer said no");
    expect(text.indexOf("CardDeclined")).toBeLessThan(text.indexOf("Timing"));
  });

  it("replaces the status message rather than repeating it", () => {
    // The status message is usually the same sentence with less in it, and two
    // red blocks saying nearly the same thing read as two different failures.
    const text = render({ exception_type: "CardDeclined", exception_message: "issuer said no" });

    expect(text).not.toContain("handler returned 500");
  });

  it("falls back to the status message when the span failed without throwing", () => {
    // A gRPC `UNAVAILABLE` is an outcome, not an exception. The API sends empty
    // strings rather than filling these in, so the pane must still say
    // something about a failure that had no exception behind it.
    const text = render({ exception_type: "", exception_message: "" });

    expect(text).toContain("handler returned 500");
  });

  it("shows the type alone when the exception carried no message", () => {
    const text = render({ exception_type: "BrokenPipe", exception_message: "" });

    expect(text).toContain("BrokenPipe");
    expect(text).not.toContain("handler returned 500");
  });
});

describe("the build a span ran on", () => {
  it("is shown when the service said which", () => {
    const text = render({ deployment_version: "2026.9.12-a1b2c3d" });

    expect(text).toContain("Build");
    expect(text).toContain("2026.9.12-a1b2c3d");
  });

  it("is absent rather than blank when it did not", () => {
    // An empty row under a term reads as "this build has no name", which is a
    // different claim from "the service did not say".
    expect(render({ deployment_version: "" })).not.toContain("Build");
  });
});

describe("a span that is a model call", () => {
  it("names the model when the span carries one", () => {
    const text = render({
      is_model_call: true,
      attributes: { gen_ai: { request: { model: "claude-fable-5-1" } } },
    });

    expect(text).toContain("Model call");
    expect(text).toContain("claude-fable-5-1");
  });

  it("says the model is unlabeled when the span carries no model name", () => {
    const text = render({
      is_model_call: true,
      attributes: { gen_ai: { provider: { name: "anthropic" } } },
    });

    expect(text).toContain("Model call");
    expect(text).toContain("unlabeled model");
  });

  it("shows no model-call row at all on an ordinary span", () => {
    expect(render({ is_model_call: false })).not.toContain("Model call");
  });
});

describe("the timeline inside a span", () => {
  it("places each event by how far into the span it happened", () => {
    // The span's own start is the only reference a reader has here. Two
    // wall-clock times would make them subtract to get the thing the timeline
    // exists to show.
    const text = render({
      start_time: "2026-09-13T10:00:00.000Z",
      events: [
        { at: "2026-09-13T10:00:00.312Z", name: "exception", attributes: {} },
        { at: "2026-09-13T10:00:00.050Z", name: "retry", attributes: {} },
      ],
    });

    expect(text).toContain("Timeline");
    expect(text).toContain("+312ms");
    expect(text).toContain("+50ms");
  });

  it("falls back to a clock time for an event before the span began", () => {
    // Clock skew between an SDK and the collector. A negative offset reads as a
    // bug in us rather than as the disagreement it is.
    const text = render({
      start_time: "2026-09-13T10:00:00.000Z",
      events: [{ at: "2026-09-13T09:59:59.000Z", name: "early", attributes: {} }],
    });

    expect(text).not.toContain("+-");
    expect(text).toContain("early");
  });

  it("shows no timeline at all on a span that had no events", () => {
    // Most spans have none, and an empty section is a heading that says nothing.
    expect(render({ events: [] })).not.toContain("Timeline");
  });
});

describe("the spans this one links to", () => {
  async function renderWithRouter(span: Partial<Span>): Promise<HTMLElement> {
    const row: WaterfallRow = {
      key: "row-1",
      span: { ...SPAN, ...span },
      depth: 0,
      childCount: 0,
      orphaned: false,
      precedesParent: false,
    };
    const rootRoute = createRootRoute({
      component: () => createElement(SpanDetail, { traceId: TRACE_ID, row, onClose: () => {} }),
    });
    const tracesRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/traces/$traceId",
      component: () => null,
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([tracesRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
    await router.load();

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    mounted.push(root);
    await act(async () => root.render(withQueries(createElement(RouterProvider, { router }))));
    return container;
  }

  it("offers each link as navigation into the trace it points at", async () => {
    const container = await renderWithRouter({
      links: [
        {
          trace_id: "4bf92f3577b34da6a3ce929d0e0e4736",
          span_id: "00f067aa0ba902b7",
          attributes: { kind: "queue" },
        },
      ],
    });
    const link = container.querySelector("a");

    expect(link?.getAttribute("href")).toBe("/traces/4bf92f3577b34da6a3ce929d0e0e4736");
    expect(container.textContent).toContain("queue");
  });

  it("calls a link linked when the producer said nothing about it", async () => {
    const container = await renderWithRouter({
      links: [{ trace_id: "4bf92f3577b34da6a3ce929d0e0e4736", span_id: "x", attributes: {} }],
    });

    expect(container.textContent).toContain("linked");
  });

  it("shows no links section on a span that has none", async () => {
    const container = await renderWithRouter({ links: [] });

    expect(container.textContent).not.toContain("Links");
    expect(container.querySelector("a")).toBeNull();
  });
});

describe("the span's profile", () => {
  async function renderAnswered(answer: object): Promise<HTMLElement> {
    const asked: URLSearchParams[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: URL) => {
        asked.push(url.searchParams);
        return new Response(JSON.stringify(answer), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
    const row: WaterfallRow = {
      key: "row-1",
      span: SPAN,
      depth: 0,
      childCount: 0,
      orphaned: false,
      precedesParent: false,
    };
    const rootRoute = createRootRoute({
      component: () => createElement(SpanDetail, { traceId: TRACE_ID, row, onClose: () => {} }),
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
    await act(async () => root.render(withQueries(createElement(RouterProvider, { router }))));
    for (let round = 0; round < 30 && !container.textContent?.includes("Profile"); round += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(asked[0]?.get("trace_id")).toBe(TRACE_ID);
    expect(asked[0]?.get("span_id")).toBe(SPAN.span_id);
    return container;
  }

  it("opens the span's own samples when the profiler linked some to it (AC10)", async () => {
    const container = await renderAnswered({ state: "linked", linked_samples: 3, types: ["cpu"] });
    const link = [...container.querySelectorAll("a")].find((a) =>
      a.textContent?.includes("Open this span's profile"),
    );

    expect(link?.textContent).toContain("3 samples");
    expect(link?.getAttribute("href")).toContain(`trace=${TRACE_ID}`);
    expect(link?.getAttribute("href")).toContain(`span=${SPAN.span_id}`);
    expect(container.textContent).toContain("Preview");

    // The screen reads the console's window: following the link pins it around the
    // span, or an older span would open on the last hour and find nothing.
    await act(async () => link?.click());
    const stored = JSON.parse(localStorage.getItem(SELECTION_STORAGE_KEY) ?? "{}");
    expect(stored.window).toEqual({
      kind: "absolute",
      from: "2026-09-13T09:55:00.000Z",
      to: "2026-09-13T10:05:00.412Z",
    });
  });

  it("says a profiled service caught no sample in this span, and why (AC10)", async () => {
    const container = await renderAnswered({ state: "no_samples", linked_samples: 0, types: [] });

    expect(container.textContent).toContain("no sample landed in this span");
    expect(container.textContent).toContain("Open the service's profile");
  });

  it("says a service that is not profiled is not, with the way to set it up", async () => {
    const container = await renderAnswered({ state: "not_profiled", linked_samples: 0, types: [] });

    expect(container.textContent).toContain("payments isn't profiled");
    expect(
      container.querySelector('a[href="/docs/profiling/continuous-profiling"]'),
    ).not.toBeNull();
  });
});
