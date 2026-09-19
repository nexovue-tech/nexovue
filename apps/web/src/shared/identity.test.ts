// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { Action } from "./identity";
import { useCan } from "./identity";

/**
 * The defect this guards: a control shown to a role the API will refuse (F26, AC4).
 * `useCan` is the one question every screen asks, so it must answer from the
 * permissions `/auth/me` sends — and answer "no" until it knows.
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

function answerMe(permissions: Action[], held?: Promise<void>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      await held;
      return new Response(
        JSON.stringify({
          user: {
            id: "u1",
            email: "dev@example.test",
            name: "Dev",
            role: "member",
            email_verified_at: null,
            permissions,
          },
          organization: { id: "o1", slug: "dev", name: "Dev Co" },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }),
  );
}

function Probe({ action }: { action: Action }) {
  return createElement("p", null, useCan(action) ? "may" : "may not");
}

async function mount(action: Action) {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client }, createElement(Probe, { action })));
  });
  return container;
}

async function settle() {
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe("useCan", () => {
  it("says yes to an action the person's role allows", async () => {
    answerMe(["operate_checks", "operate_alerts"]);
    const container = await mount("operate_checks");
    await settle();
    expect(container.textContent).toBe("may");
  });

  it("says no to an action the role does not allow", async () => {
    answerMe(["operate_checks"]);
    const container = await mount("manage_channels");
    await settle();
    expect(container.textContent).toBe("may not");
  });

  it("says no until it knows, so a control never appears and then vanishes", async () => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    answerMe(["manage_channels"], held);
    const container = await mount("manage_channels");
    expect(container.textContent).toBe("may not");
    release();
    await settle();
    expect(container.textContent).toBe("may");
  });
});
