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
import { AuditScreen } from "./AuditScreen";
import type { AuditEntry } from "./api";

/**
 * The defects these guard (F26, FR36, AC38): an audit log a member can open, entries
 * that read as action codes instead of sentences, and a support view that looks like
 * anyone else's click.
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

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

const ENTRIES: AuditEntry[] = [
  {
    id: "e2",
    at: "2026-09-19T10:00:00Z",
    actor: { kind: "person", id: "u1", name: "Raj" },
    action: "member.role_changed",
    target: "user:u2",
    target_name: "Priya",
    detail: { before: "member", after: "viewer" },
    impersonation_context: null,
  },
  {
    id: "e1",
    at: "2026-09-19T09:00:00Z",
    actor: { kind: "staff", id: null, name: "Raj at nexovue" },
    action: "support.view_request",
    target: "user:u2",
    target_name: "Priya",
    detail: null,
    impersonation_context: "i1",
  },
];

function stubApi(role: "owner" | "member") {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      if (url.pathname.endsWith("/auth/me")) {
        return json({
          user: {
            id: "u1",
            email: "raj@example.test",
            name: "Raj",
            role,
            email_verified_at: null,
            permissions: role === "owner" ? ["read_audit"] : ["operate_checks"],
          },
          organization: { id: "o1", slug: "acme", name: "Acme" },
        });
      }
      if (url.pathname.endsWith("/audit")) {
        asked.push(url.search);
        return json({ entries: ENTRIES, next_cursor: null });
      }
      if (url.pathname.endsWith("/members")) return json({ members: [] });
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
  return asked;
}

async function settle() {
  for (let round = 0; round < 10; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount() {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings/audit",
    component: () => createElement(QueryClientProvider, { client }, createElement(AuditScreen)),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screen]),
    history: createMemoryHistory({ initialEntries: ["/settings/audit"] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return container;
}

describe("the audit log screen", () => {
  it("reads each entry as a sentence and marks what support did", async () => {
    stubApi("owner");
    const container = await mount();

    const rows = [...container.querySelectorAll('ol[aria-label="Audit entries"] > li')];
    expect(rows[0]?.textContent).toContain("Raj changed Priya's role from Member to Viewer");
    expect(rows[1]?.textContent).toContain("nexovue support (Raj at nexovue)");
    expect(rows[1]?.textContent).toContain("Support view");
  });

  it("refuses a member, and never asks the API", async () => {
    const asked = stubApi("member");
    const container = await mount();

    expect(container.textContent).toContain("Your role (Member) cannot open this.");
    expect(asked).toEqual([]);
  });
});
