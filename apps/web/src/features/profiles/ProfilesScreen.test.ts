// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProfiledService } from "@/shared/profiles/api";
import { setSelectedProjectId } from "@/shared/selection";

import type { ComparisonResponse, FlameGraphResponse, ProfileFilters } from "./api";
import { ProfilesScreen } from "./ProfilesScreen";
import { parseProfilesSearch } from "./search";

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0099";
const mounted: Root[] = [];

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const checkout: ProfiledService = {
  service_name: "checkout",
  types: [
    { sample_type: "alloc_space", unit: "bytes" },
    { sample_type: "cpu", unit: "nanoseconds" },
  ],
  last_received: new Date(Date.now() - 60_000).toISOString(),
};

function graph(overrides: Partial<FlameGraphResponse> = {}): FlameGraphResponse {
  return {
    sample_type: "cpu",
    unit: "nanoseconds",
    total: 2_000_000_000,
    samples: 200,
    merged_frames: 0,
    root: {
      name: "all",
      file: "",
      unsymbolized: false,
      total: 2_000_000_000,
      self_value: 0,
      merged: 0,
      children: [
        {
          name: "main.(*Cart).Checkout",
          file: "cart.go",
          unsymbolized: false,
          total: 2_000_000_000,
          self_value: 2_000_000_000,
          merged: 0,
          children: [],
        },
      ],
    },
    functions: [
      {
        name: "main.(*Cart).Checkout",
        file: "cart.go",
        unsymbolized: false,
        self_value: 2_000_000_000,
        total: 2_000_000_000,
      },
    ],
    ...overrides,
  };
}

const NO_FILTERS: ProfileFilters = { hosts: [], versions: [], environments: [] };

interface Api {
  services?: ProfiledService[];
  graph?: FlameGraphResponse;
  filters?: ProfileFilters;
  latest?: string | null;
  compare?: ComparisonResponse;
}

/** Every request the screen made, by path, with its query. */
type Calls = Record<string, URLSearchParams[]>;

function stubApi(api: Api): Calls {
  const calls: Calls = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      const path = url.pathname.split("/profiles/")[1] ?? "";
      calls[path] = [...(calls[path] ?? []), url.searchParams];
      const bodies: Record<string, unknown> = {
        services: { services: api.services ?? [] },
        graph: api.graph ?? graph(),
        filters: api.filters ?? NO_FILTERS,
        latest: { last_received: api.latest ?? null },
        timeline: { unit: "nanoseconds", bucket_seconds: 60, points: [] },
        compare: api.compare,
      };
      return new Response(JSON.stringify(bodies[path]), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return calls;
}

async function mount(url = "/profiles") {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setSelectedProjectId(PROJECT_ID);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute({ component: Outlet });
  const profilesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/profiles",
    validateSearch: parseProfilesSearch,
    component: () => createElement(ProfilesScreen, { search: profilesRoute.useSearch() }),
  });
  const traceRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/traces/$traceId",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([profilesRoute, traceRoute]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
        createElement(RouterProvider, { router } as any),
      ),
    ),
  );
  return { container, router };
}

async function settledText(container: HTMLElement, expected: string): Promise<string> {
  for (let round = 0; round < 60; round += 1) {
    const text = (container.textContent ?? "").replace(/\s+/g, " ");
    if (text.includes(expected)) return text;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  throw new Error(`gave up waiting for "${expected}"; the page reads: ${container.textContent}`);
}

async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("the Profiles screen", () => {
  it("says a project with no profiles has none yet, and links the setup page", async () => {
    stubApi({ services: [], latest: null });
    const { container } = await mount();

    const text = await settledText(container, "No profiles yet");
    expect(text).toContain("eBPF profiler");
    expect(
      container.querySelector('a[href="/docs/profiling/continuous-profiling"]'),
    ).not.toBeNull();
    // The preview label is on every profiling screen, the empty one too (AC16).
    expect(text).toContain("Preview");
  });

  it("points an empty window at the newest profile the project has", async () => {
    stubApi({ services: [], latest: new Date(Date.now() - 3 * 3_600_000).toISOString() });
    const { container } = await mount();

    const text = await settledText(container, "No profiles in this range");
    expect(text).toContain("Show the hour of the newest profile");
  });

  it("opens the first service on CPU, whatever order the types came in", async () => {
    const calls = stubApi({ services: [checkout] });
    const { container } = await mount();

    const text = await settledText(container, "main.(*Cart).Checkout");
    expect(calls.graph?.[0]?.get("service_name")).toBe("checkout");
    expect(calls.graph?.[0]?.get("type")).toBe("cpu");
    expect(text).toContain("CPU time · 2.0 s across 200 samples");
    // The answer in words leads; the graph is its evidence (principles rule 1).
    expect(container.querySelector("#flame-graph")?.textContent).toBe(
      "main.(*Cart).Checkout accounts for all of checkout's CPU time.",
    );
    expect(text).not.toContain("profiler may have stopped");
    expect(text).toContain("Preview");
  });

  it("says a range with nothing in it is empty, never an empty graph (AC12)", async () => {
    stubApi({ services: [checkout], graph: graph({ samples: 0, total: 0 }) });
    const { container } = await mount();

    await settledText(container, "No profiles in this range");
    expect(container.querySelector('[aria-label="Flame graph"]')).toBeNull();
  });

  it("says a filter matched nothing and offers to clear it, rather than 'no profiles'", async () => {
    stubApi({ services: [checkout], graph: graph({ samples: 0, total: 0 }) });
    const { container } = await mount("/profiles?host=web-9");

    await settledText(container, "No samples match these filters");
    expect(container.textContent).toContain("Clear the filters");
  });

  it("says a service named in the link is not profiled, with the setup page and the ones that are", async () => {
    stubApi({ services: [checkout], latest: null });
    const { container } = await mount("/profiles?service=cart");

    const text = await settledText(container, "cart isn't profiled");
    expect(text).toContain("Profiled");
    expect(text).toContain("checkout");
    expect(
      container.querySelector('a[href="/docs/profiling/continuous-profiling"]'),
    ).not.toBeNull();
  });

  it("offers the hosts, versions and environments the profile carries, and narrows by them (AC8)", async () => {
    const calls = stubApi({
      services: [checkout],
      filters: {
        hosts: [{ value: "web-1", samples: 120, last_seen: checkout.last_received }],
        versions: [{ value: "", samples: 80, last_seen: checkout.last_received }],
        environments: [],
      },
    });
    const { container } = await mount("/profiles?host=web-1&version=(not stated)");

    await settledText(container, "web-1 (120)");
    expect(container.textContent).toContain("(not stated) (80)");
    const asked = calls.graph?.at(-1);
    expect(asked?.get("host")).toBe("web-1");
    // "Not stated" is the empty value to the API: samples that named no version.
    expect(asked?.get("version")).toBe("");
  });

  it("highlights the frames a search matches and their share (AC8)", async () => {
    stubApi({ services: [checkout] });
    const { container } = await mount();
    await settledText(container, "main.(*Cart).Checkout");

    const search = container.querySelector<HTMLInputElement>(
      'input[aria-label="Search functions"]',
    );
    if (!search) throw new Error("no search box");
    await type(search, "checkout");

    const text = await settledText(container, "1 frame matches");
    expect(text).toContain("100 % of the total");
  });

  it("reads only one span's samples when the link names a span, and leads back to its trace", async () => {
    const trace = "a".repeat(32);
    const span = "b".repeat(16);
    const calls = stubApi({ services: [checkout] });
    const { container } = await mount(`/profiles?service=checkout&trace=${trace}&span=${span}`);

    await settledText(container, "Only the samples recorded during one span.");
    expect(calls.graph?.[0]?.get("trace_id")).toBe(trace);
    expect(calls.graph?.[0]?.get("span_id")).toBe(span);
    expect(container.querySelector(`a[href="/traces/${trace}"]`)).not.toBeNull();
  });

  it("refuses to compare a version with itself, beside the pickers", async () => {
    const calls = stubApi({
      services: [checkout],
      filters: {
        hosts: [],
        versions: [
          { value: "2.0.0", samples: 5, last_seen: new Date().toISOString() },
          { value: "1.0.0", samples: 5, last_seen: new Date(Date.now() - 60_000).toISOString() },
        ],
        environments: [],
      },
    });
    const { container } = await mount("/profiles?compare=versions&base=2.0.0&against=2.0.0");

    await settledText(container, "Pick two different versions");
    expect(calls.compare).toBeUndefined();
  });

  it("compares the two latest versions by default and ranks what grew (AC9)", async () => {
    const compare: ComparisonResponse = {
      sample_type: "cpu",
      unit: "nanoseconds",
      baseline_samples: 10,
      comparison_samples: 10,
      merged_frames: 0,
      root: {
        name: "all",
        file: "",
        unsymbolized: false,
        merged: 0,
        weight: 1_000_000_000,
        baseline_share: 1,
        comparison_share: 1,
        children: [
          {
            name: "sha256.block",
            file: "",
            unsymbolized: false,
            merged: 0,
            weight: 300_000_000,
            baseline_share: 0.2,
            comparison_share: 0.4,
            children: [],
          },
        ],
      },
      functions: [
        {
          name: "sha256.block",
          file: "",
          unsymbolized: false,
          baseline_self: 0.2,
          comparison_self: 0.4,
          baseline_total: 0.2,
          comparison_total: 0.4,
          change: 0.2,
        },
      ],
    };
    const calls = stubApi({
      services: [checkout],
      compare,
      filters: {
        hosts: [],
        versions: [
          { value: "1.0.0", samples: 50, last_seen: new Date(Date.now() - 600_000).toISOString() },
          { value: "2.0.0", samples: 5, last_seen: new Date().toISOString() },
        ],
        environments: [],
      },
    });
    const { container } = await mount("/profiles?compare=versions");

    const text = await settledText(container, "+20.0 pts");
    expect(calls.compare?.[0]?.get("base_version")).toBe("1.0.0");
    expect(calls.compare?.[0]?.get("version")).toBe("2.0.0");
    expect(text).toContain("a larger share");
    expect(container.querySelector('[data-change="more"]')).not.toBeNull();
  });

  it("says a profiler that stopped sending may have stopped, not that the service went quiet (US17.8)", async () => {
    const stale = { ...checkout, last_received: new Date(Date.now() - 45 * 60_000).toISOString() };
    stubApi({ services: [stale] });
    const { container } = await mount();

    await settledText(container, "its profiler may have stopped");
  });
});
