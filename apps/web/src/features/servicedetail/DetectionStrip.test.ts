// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ServiceDetection } from "@/shared/detection/api";
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";

import { DetectionStrip } from "./DetectionStrip";

/** A service's detection strip: its snooze and mute, and what each says it did (FR22). */

const PROJECT_ID = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0200";
const IN_AN_HOUR = new Date(Date.now() + 3_600_000).toISOString();
const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

const DETECTION: ServiceDetection = {
  state: "judged",
  signals: [],
  muted_until: null,
  snoozed_until: null,
  findings: [],
  truncated: false,
};

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

/** Every answer is held until `release` is called, so a test can look while it is pending. */
function stubApi(role: TestRole) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/auth/me") return json(identityFor(role));
      const body = JSON.parse(String(init?.body ?? "null"));
      calls.push({ method, path: url.pathname, body });
      await held;
      const snoozing = method === "POST" && body?.minutes !== null;
      return json({ muted_until: null, snoozed_until: snoozing ? IN_AN_HOUR : null });
    }),
  );
  return { calls, release: () => release() };
}

async function settle(rounds = 6) {
  for (let round = 0; round < rounds; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(detection: ServiceDetection) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () =>
      createElement(
        QueryClientProvider,
        { client },
        createElement(DetectionStrip, {
          projectId: PROJECT_ID,
          serviceName: "checkout",
          detection,
          failed: false,
          zone: "UTC",
        }),
      ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([page]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return container;
}

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();

function button(container: Element, label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((each) => text(each) === label);
  if (!found) throw new Error(`no button "${label}" in: ${text(container)}`);
  return found;
}

describe("the detection strip", () => {
  it("snoozes for a length the server counts, and says until when", async () => {
    const api = stubApi("member");
    const container = await mount(DETECTION);

    await act(async () => button(container, "Snooze").click());
    await act(async () => button(container, "Snooze checkout").click());
    api.release();
    await settle();

    expect(api.calls).toEqual([
      {
        method: "POST",
        path: "/api/v1/services/checkout/snooze",
        body: { project_id: PROJECT_ID, minutes: 30 },
      },
    ]);
    expect(text(container.querySelector('[role="status"]') as Element)).toContain(
      "checkout is snoozed until",
    );
  });

  it("moves focus into the choice when it opens", async () => {
    stubApi("member");
    const container = await mount(DETECTION);

    await act(async () => button(container, "Snooze").click());

    const panel = container.querySelector("fieldset");
    expect(panel?.contains(document.activeElement)).toBe(true);
  });

  it("shows ending a snooze as busy, then says it ended", async () => {
    const api = stubApi("member");
    const container = await mount({ ...DETECTION, snoozed_until: IN_AN_HOUR });
    const status = container.querySelector('[role="status"]');
    expect(status).not.toBeNull();

    await act(async () => button(container, "End snooze").click());
    expect(button(container, "Ending the snooze").getAttribute("aria-busy")).toBe("true");
    api.release();
    await settle();

    expect(text(status as Element)).toBe("The snooze on checkout has ended.");
  });

  it("shows an unmute as busy, then says it ended", async () => {
    const api = stubApi("admin");
    const container = await mount({ ...DETECTION, state: "muted", muted_until: IN_AN_HOUR });

    await act(async () => button(container, "Unmute").click());
    expect(button(container, "Unmuting").disabled).toBe(true);
    api.release();
    await settle();

    expect(text(container.querySelector('[role="status"]') as Element)).toBe(
      "checkout is no longer muted.",
    );
  });
});
