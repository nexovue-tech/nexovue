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
