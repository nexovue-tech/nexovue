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

import type { LLMCallSummary } from "./api";
import { llmCallFilters, llmCallsNarrowed, MODEL_KEY, SERVICE_KEY } from "./filters";
import type { LLMCallsViewProps } from "./LLMCallsScreen";
import { LLMCallsView } from "./LLMCallsScreen";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

const COMPLETE_CALL: LLMCallSummary = {
  service_name: "payment",
  span_name: "chat claude-fable-5-1",
  model_name: "claude-fable-5-1",
  call_count: 12,
  p50_ms: 420,
  p95_ms: 980,
  error_count: 0,
  input_tokens: 842,
  output_tokens: 156,
  cost: null,
};

const UNLABELED_CALL: LLMCallSummary = {
  service_name: "payment",
  span_name: "chat",
  model_name: "unlabeled model",
  call_count: 3,
  p50_ms: 310,
  p95_ms: 500,
  error_count: 1,
  input_tokens: null,
  output_tokens: null,
  cost: null,
};

const BASE: LLMCallsViewProps = {
  windowLabel: "the last hour",
  calls: [COMPLETE_CALL, UNLABELED_CALL],
  isPending: false,
  isError: false,
  onRetry: () => {},
  typedService: "",
  onTypedService: () => {},
  typedModel: "",
  onTypedModel: () => {},
  narrowed: false,
  onClearFilters: () => {},
};

async function render(overrides: Partial<LLMCallsViewProps> = {}): Promise<HTMLElement> {
  const rootRoute = createRootRoute({
    component: () => createElement(LLMCallsView, { ...BASE, ...overrides }),
  });
  const children = ["/llm", "/traces"].map((path) =>
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

const readable = async (overrides: Partial<LLMCallsViewProps> = {}) =>
  ((await render(overrides)).textContent ?? "").replace(/\s+/g, " ").trim();

describe("the four states", () => {
  it("waits with a skeleton, never a spinner", async () => {
    const container = await render({ isPending: true });

    expect(container.querySelector('[aria-label="Loading the model calls"]')).not.toBeNull();
  });

  it("shows a failed load inline, with a retry", async () => {
    const text = await readable({ isError: true, error: new Error("boom") });

    expect(text).toContain("Model calls could not be loaded");
  });

  it("explains an empty window rather than showing an empty table", async () => {
    const text = await readable({ calls: [] });

    expect(text).toContain("No model calls seen in this window");
  });

  it("lists every call group, including the unlabeled-model one", async () => {
    const text = await readable();

    expect(text).toContain("claude-fable-5-1");
    expect(text).toContain("unlabeled model");
    expect(text).toContain("842");
  });
});

describe("a group missing a reading", () => {
  it("renders an absent token count as an em dash, never a zero", async () => {
    const text = await readable();

    // The unlabeled group reported no tokens at all — an absence, not "0".
    expect(text).toContain("—");
  });
});

describe("a model call's own spans", () => {
  it("links the span into the existing Traces screen, narrowed to it", async () => {
    const container = await render();
    const link = [...container.querySelectorAll("a")].find(
      (a) => a.textContent === "chat claude-fable-5-1",
    );

    expect(link?.getAttribute("href")).toBe(
      "/traces?service=payment&operation=chat+claude-fable-5-1",
    );
  });
});

describe("searching by service or model", () => {
  it("reports every keystroke in either box", async () => {
    const typedServices: string[] = [];
    const typedModels: string[] = [];
    const container = await render({
      onTypedService: (value) => typedServices.push(value),
      onTypedModel: (value) => typedModels.push(value),
    });

    type(searchBox(container, "Search services"), "payment");
    type(searchBox(container, "Search models"), "claude");

    expect(typedServices).toEqual(["payment"]);
    expect(typedModels).toEqual(["claude"]);
  });

  it("says a search matched nothing rather than that nothing ever arrived", async () => {
    const text = await readable({ calls: [], narrowed: true, typedService: "nope" });

    expect(text).toContain("No model calls match this search");
    expect(text).not.toContain("No model calls seen in this window");
  });

  it("offers the way out of a search that matched nothing", async () => {
    let cleared = 0;
    const container = await render({
      calls: [],
      narrowed: true,
      onClearFilters: () => {
        cleared += 1;
      },
    });

    const clear = [...container.querySelectorAll("button")].find((button) =>
      (button.textContent ?? "").includes("Clear search"),
    );
    act(() => clear?.click());

    expect(cleared).toBe(1);
  });
});

describe("what the URL holds", () => {
  it("reads both filters back out of a shared link", async () => {
    expect(llmCallFilters({ [SERVICE_KEY]: "payment", [MODEL_KEY]: "claude" })).toEqual({
      [SERVICE_KEY]: "payment",
      [MODEL_KEY]: "claude",
    });
  });

  it("treats a blank value as not filtering", async () => {
    expect(llmCallFilters({ [SERVICE_KEY]: "   " })).toEqual({});
  });

  it("counts either filter as narrowing", async () => {
    expect(llmCallsNarrowed({})).toBe(false);
    expect(llmCallsNarrowed({ [SERVICE_KEY]: "payment" })).toBe(true);
    expect(llmCallsNarrowed({ [MODEL_KEY]: "claude" })).toBe(true);
  });
});

function searchBox(container: HTMLElement, label: string): HTMLInputElement {
  const found = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!found) throw new Error(`no search box labelled "${label}" rendered`);
  return found;
}

/**
 * React tracks an input's value and drops an event whose value it believes it
 * already has, so a plain assignment here would silently swallow the keystroke.
 */
function type(input: HTMLInputElement, value: string): void {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  act(() => {
    setValue?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
