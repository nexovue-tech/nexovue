// @vitest-environment jsdom

/**
 * The refresh control reads the freshest data in the cache, and every screen
 * creates queries as it mounts. Those two facts met inside React's render phase:
 * React Query emits `added` from within `useQuery`'s first render, this component
 * subscribed to every cache event, and so a screen rendering set state in a
 * component that was already mounted.
 *
 * React only reports that as a console error, which is exactly the kind of defect a
 * suite that asserts on the DOM never notices — so this test asserts on the console.
 */

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FixedIntervalRefresh } from "@/shared/refresh";
import { declareStream, setPaused } from "@/shared/refresh";

import { RefreshControl } from "./RefreshControl";

let container: HTMLElement;
let root: Root;
let errors: string[];

beforeEach(() => {
  errors = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    errors.push(args.map(String).join(" "));
  });
  container = document.createElement("div");
  document.body.append(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

/**
 * A screen mounting a query that already has an answer.
 *
 * The data is the load-bearing part: React Query emits `added` from inside this
 * component's first render, and only a query that arrives *with* data moves the
 * "freshest reading" the control shows. A query added empty leaves that value
 * unchanged, React bails out of the update, and the defect stays invisible — which
 * is why an earlier version of this test needed a random key to fail at all.
 */
function ScreenWithANewQuery({ children }: { children?: ReactNode }) {
  useQuery({
    queryKey: ["already-answered"],
    queryFn: async () => 1,
    initialData: 1,
    initialDataUpdatedAt: Date.now(),
  });
  return createElement("div", null, children);
}

describe("the refresh control beside a screen that is mounting", () => {
  it("does not update itself while that screen renders", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    root = createRoot(container);

    // The order is the point, and it is the order a navigation produces: the control
    // is already mounted and subscribed, and then a screen renders a query.
    const show = async (screen: boolean) => {
      await act(async () => {
        root.render(
          createElement(
            QueryClientProvider,
            { client },
            createElement(RefreshControl),
            screen ? createElement(ScreenWithANewQuery) : null,
          ),
        );
      });
    };

    await show(false);
    await show(true);
    // The microtask the subscription defers its read to.
    await act(async () => {
      await Promise.resolve();
    });

    const offending = errors.filter((line) => line.includes("while rendering a different"));
    expect(offending, offending.join("\n")).toEqual([]);
  });
});

/**
 * One test per row of the design's refresh state table (F20, T6.3, AC104): what the control
 * shows, whether Pause exists, and that an interval is never shown over a fixed window.
 * Instants are fixed and the clock is pinned.
 */
describe("the refresh control while a dashboard is open", () => {
  const LAST_GOOD = "2026-11-11T02:14:00.000Z";
  const base = (over: Partial<FixedIntervalRefresh> = {}): FixedIntervalRefresh => ({
    kind: "fixed-interval",
    fixedWindow: false,
    savedIntervalMs: 60_000,
    intervalMs: 60_000,
    tabIntervalMs: undefined,
    chooseInterval: () => {},
    lastGoodAt: LAST_GOOD,
    zone: "UTC",
    reading: false,
    readNow: () => {},
    chooseRange: () => {},
    ...over,
  });

  async function show(declaration: FixedIntervalRefresh) {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-11T02:20:00.000Z"));
    declareStream(declaration);
    root = createRoot(container);
    await act(async () => {
      root.render(
        createElement(
          QueryClientProvider,
          { client: new QueryClient() },
          createElement(RefreshControl),
        ),
      );
    });
    return container.textContent ?? "";
  }

  const pauseButton = () => container.querySelector('button[aria-label$="refreshing"]');
  const buttons = () => [...container.querySelectorAll("button")].map((b) => b.textContent);

  afterEach(() => {
    declareStream(undefined);
    setPaused(false);
    vi.useRealTimers();
  });

  it("live: the tab's interval, Read now, the age, and Pause", async () => {
    const text = await show(base());
    expect(text).toContain("Every");
    expect(text).toContain("updated 6m ago");
    expect(buttons()).toContain("Read now");
    expect(pauseButton()?.getAttribute("aria-label")).toBe("Pause refreshing");
  });

  it("fixed window: 'Fixed time', no Pause, no interval, nothing to read now (AC104)", async () => {
    const text = await show(base({ fixedWindow: true }));
    expect(text).toBe("Fixed time");
    expect(pauseButton()).toBeNull();
    expect(container.querySelector("select, [role=combobox], button")).toBeNull();
  });

  it("checks fixed before the interval, so a fixed window never says 'Every minute'", async () => {
    const text = await show(base({ fixedWindow: true, intervalMs: 60_000 }));
    expect(text).not.toContain("Every");
  });

  it("paused: a filled chip naming the last good reading on the project's clock, and Resume", async () => {
    setPaused(true);
    const text = await show(base({ zone: "Asia/Kolkata" }));
    expect(text).toContain("Paused · showing 07:44 Kolkata time");
    expect(pauseButton()?.getAttribute("aria-label")).toBe("Resume refreshing");
  });

  it("refresh off: 'Refresh off · showing 02:14' and Read now, and no Pause", async () => {
    const text = await show(base({ savedIntervalMs: false, intervalMs: false }));
    expect(text).toContain("Refresh off · showing 02:14 UTC");
    expect(buttons()).toContain("Read now");
    expect(pauseButton()).toBeNull();
  });

  it("returned from a hidden tab: 'reading…' until the first read answers", async () => {
    const text = await show(base({ reading: true }));
    expect(text).toContain("reading…");
  });

  it("takes its age from the last good reading and not from any answer in the cache", async () => {
    const text = await show(base({ lastGoodAt: "2026-11-11T02:10:00.000Z" }));
    expect(text).toContain("updated 10m ago");
  });

  it("Read now asks the screen to read", async () => {
    const readNow = vi.fn();
    await show(base({ readNow }));
    const button = [...container.querySelectorAll("button")].find(
      (b) => b.textContent === "Read now",
    );
    act(() => button?.click());
    expect(readNow).toHaveBeenCalledTimes(1);
  });

  it("is on the project's clock, not the browser's (AC64)", async () => {
    setPaused(true);
    const text = await show(base({ zone: "America/New_York" }));
    expect(text).toContain("21:14");
  });
});
