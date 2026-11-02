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

import type { Action } from "@/shared/identity";

import type { Member } from "./api";
import { MembersScreen } from "./MembersScreen";

/**
 * The defects these guard (F26):
 *
 * 1. A viewer offered controls the API will refuse (AC4, BR7).
 * 2. A role above the signed-in person's own offered as a choice (BR3): an admin must
 *    never see "Owner" in a select, and must not be able to act on an owner at all.
 * 3. A pending invite that failed to send reading like one that went.
 */

const OWNER_ID = "00000000-0000-0000-0000-000000000001";

function person(overrides: Partial<Member>): Member {
  return {
    id: crypto.randomUUID(),
    email: "someone@example.test",
    name: "Someone",
    role: "member",
    status: "active",
    invited_by: null,
    invited_at: null,
    invite_sent_at: null,
    invite_expires_at: null,
    added_at: "2026-09-01T09:00:00Z",
    last_signed_in_at: "2026-09-19T09:00:00Z",
    ...overrides,
  };
}

const MEMBERS: Member[] = [
  person({ id: OWNER_ID, email: "raj@example.test", name: "Raj", role: "owner" }),
  person({ email: "priya@example.test", name: "Priya", role: "member" }),
  person({
    email: "new@example.test",
    name: null,
    role: "viewer",
    status: "invited",
    invited_by: "Raj",
    invited_at: "2026-09-19T08:00:00Z",
    invite_sent_at: null,
    invite_expires_at: "2026-09-26T08:00:00Z",
  }),
];

const PERMISSIONS: Record<string, Action[]> = {
  owner: [
    "operate_checks",
    "operate_alerts",
    "upload_source_maps",
    "manage_channels",
    "manage_check_secrets",
    "configure_projects",
    "configure_detection",
    "manage_credentials",
    "manage_members",
    "manage_owners",
    "read_audit",
    "commit_spend",
    "end_organization",
  ],
  admin: [
    "operate_checks",
    "operate_alerts",
    "upload_source_maps",
    "manage_channels",
    "manage_check_secrets",
    "configure_projects",
    "configure_detection",
    "manage_credentials",
    "manage_members",
    "read_audit",
  ],
  viewer: [],
};

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

function stubApi(role: "owner" | "admin" | "viewer") {
  const selfId = role === "owner" ? OWNER_ID : "00000000-0000-0000-0000-00000000000a";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL) => {
      if (url.pathname.endsWith("/auth/me")) {
        return json({
          user: {
            id: selfId,
            email: `${role}@example.test`,
            name: role,
            role,
            email_verified_at: null,
            permissions: PERMISSIONS[role],
          },
          organization: { id: "o1", slug: "acme", name: "Acme" },
        });
      }
      if (url.pathname.endsWith("/members")) return json({ members: MEMBERS });
      throw new Error(`no stub for ${url.pathname}`);
    }),
  );
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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rootRoute = createRootRoute();
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings/members",
    component: () => createElement(QueryClientProvider, { client }, createElement(MembersScreen)),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([screen]),
    history: createMemoryHistory({ initialEntries: ["/settings/members"] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return container;
}

function row(container: HTMLElement, name: string): HTMLElement {
  const found = [...container.querySelectorAll<HTMLElement>('ul[aria-label="Members"] > li')].find(
    (item) => item.textContent?.includes(name),
  );
  if (!found) throw new Error(`no row for ${name}`);
  return found;
}

/**
 * Every control the *screen* offers. The settings rail is excluded on purpose:
 * its "All settings" disclosure is navigation chrome shown at phone width, and
 * counting it here would make this suite fail on a layout change rather than on
 * a permission one.
 */
const buttons = (element: HTMLElement) =>
  [...element.querySelectorAll("button")]
    .filter((button) => button.closest('nav[aria-label="Settings"]') === null)
    .map((button) => button.textContent?.trim());

const optionsIn = (element: HTMLElement) =>
  [...element.querySelectorAll("option")].map((option) => option.textContent);

describe("the Members screen", () => {
  it("shows a viewer everyone, and not one control", async () => {
    stubApi("viewer");
    const container = await mount();

    expect(container.textContent).toContain("Raj");
    expect(container.textContent).toContain("Priya");
    expect(container.querySelector('section[aria-label="Invite"]')).toBeNull();
    expect(container.querySelectorAll("select")).toHaveLength(0);
    expect(buttons(container)).toEqual([]);
  });

  it("offers an owner every role and every action", async () => {
    stubApi("owner");
    const container = await mount();

    const invite = container.querySelector<HTMLElement>('section[aria-label="Invite"]');
    expect(invite).not.toBeNull();
    expect(optionsIn(invite as HTMLElement)).toEqual(["Owner", "Admin", "Member", "Viewer"]);
    expect(buttons(row(container, "Priya"))).toContain("Remove");
    expect(buttons(row(container, "new@example.test"))).toEqual(["Resend", "Withdraw"]);
  });

  it("never offers an admin a role above their own, nor a hand on an owner", async () => {
    stubApi("admin");
    const container = await mount();

    const invite = container.querySelector<HTMLElement>('section[aria-label="Invite"]');
    expect(optionsIn(invite as HTMLElement)).toEqual(["Admin", "Member", "Viewer"]);
    const owner = row(container, "Raj");
    expect(owner.querySelector("select")).toBeNull();
    expect(buttons(owner)).toEqual([]);
    expect(optionsIn(row(container, "Priya"))).not.toContain("Owner");
  });

  it("says when an invite's email did not go", async () => {
    stubApi("owner");
    const container = await mount();

    expect(row(container, "new@example.test").textContent).toContain(
      "The email did not go — resend it.",
    );
  });
});
