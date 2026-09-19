// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { minutesLeft, SupportBanner } from "./SupportBanner";

/**
 * The strip that says a screen is being read by nexovue support (F31): present on
 * every screen of a support view, absent otherwise, and counting down to the end.
 */

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

function signedIn(supportView: { staff: string; until: string } | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            user: {
              id: "u1",
              email: "priya@acme.test",
              name: "Priya",
              role: "member",
              email_verified_at: null,
              permissions: [],
            },
            organization: { id: "o1", slug: "acme", name: "Acme" },
            support_view: supportView,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    ),
  );
}

async function mount() {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client }, createElement(SupportBanner)));
  });
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return container;
}

describe("the support banner", () => {
  it("names who is viewing, as whom, and how long is left", async () => {
    signedIn({
      staff: "Raj at nexovue",
      until: new Date(Date.now() + 29 * 60_000 - 1_000).toISOString(),
    });
    const container = await mount();

    expect(container.textContent).toContain("Support view, read-only.");
    expect(container.textContent).toContain("Raj at nexovue is viewing as Priya.");
    expect(container.textContent).toContain("29 min left.");
    expect(container.querySelector("button")?.textContent).toBe("End now");
  });

  it("is absent when the person is signed in as themselves", async () => {
    signedIn(null);
    const container = await mount();

    expect(container.textContent).toBe("");
  });
});

describe("minutes left", () => {
  const now = Date.parse("2026-09-20T09:00:00Z");

  it("rounds up, so the last minute still reads as one", () => {
    expect(minutesLeft("2026-09-20T09:00:30Z", now)).toBe(1);
  });

  it("never reads below zero once the view has lapsed", () => {
    expect(minutesLeft("2026-09-20T08:59:00Z", now)).toBe(0);
  });
});
