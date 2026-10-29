// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { HostedOnly } from "./HostedOnly";

/**
 * Billing and Support access on a customer's own install answer as an address with no screen,
 * not as the screen failing to load (found by the design check, 2026-10-29).
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

function installIs(deployment: "hosted" | "self-hosted") {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      if (url.pathname.endsWith("/system/info")) {
        const info = { service: "api", revision: "x", build_time: "x", deployment };
        return new Response(JSON.stringify(info), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
}

async function render() {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRoot(container);
  mounted.push(root);
  await act(async () =>
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(HostedOnly, { fallback: "No screen at this address", children: "Billing" }),
      ),
    ),
  );
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return container.textContent;
}

describe("a hosted-only screen", () => {
  it("is the screen on nexovue's hosted service", async () => {
    installIs("hosted");
    expect(await render()).toBe("Billing");
  });

  it("is an address with no screen on a customer's own install", async () => {
    installIs("self-hosted");
    expect(await render()).toBe("No screen at this address");
  });
});
