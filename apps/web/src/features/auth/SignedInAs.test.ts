// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { SignedInAs } from "./SignedInAs";

/**
 * The gap this closes (`docs/changes/console-review-fixes/`): the console had no
 * ordinary sign-out. The only way out was *Sign out everywhere*, which ends the
 * session on every device the account is signed in on — the wrong control for
 * leaving a shared machine, and four clicks deep either way.
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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function stubApi(calls: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname.endsWith("/auth/logout")) {
        calls.push(`${init?.method ?? "GET"} ${url.pathname}`);
        return new Response(null, { status: 204 });
      }
      return json({
        user: {
          id: "u1",
          email: "raj@nexovue.com",
          name: "Raj",
          role: "owner",
          email_verified_at: null,
          permissions: [],
        },
        organization: { id: "o1", slug: "acme", name: "Acme" },
      });
    }),
  );
}

async function mount() {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(createElement(QueryClientProvider, { client }, createElement(SignedInAs))),
  );
  for (let round = 0; round < 6; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return container;
}

describe("the way out of the console", () => {
  it("names the account it is about to sign out of", async () => {
    // Two accounts on one machine is the ordinary case for wanting this at all.
    stubApi([]);
    const container = await mount();

    expect(container.textContent).toContain("raj@nexovue.com");
  });

  it("ends this session, and says nothing about the others", async () => {
    const calls: string[] = [];
    stubApi(calls);
    const container = await mount();
    const button = container.querySelector("button");

    expect(button?.textContent).toBe("Sign out");
    await act(async () => button?.click());

    expect(calls).toEqual(["POST /api/v1/auth/logout"]);
  });
});
