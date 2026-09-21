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

import { ApiError } from "@/shared/api-client";
import { PERMISSIONS_OF, type TestRole } from "@/shared/identity.test-util";

import { OrganizationScreen } from "./OrganizationScreen";
import { PlatformScreen } from "./PlatformScreen";

/**
 * The defects these guard (`docs/changes/super-admin-in-the-console/`):
 *
 * 1. A customer reaching a platform address and seeing anything but a refusal.
 *    The API refuses regardless; a screen that renders controls first tells them
 *    the area exists.
 * 2. A lapsed re-authentication rendering as a failure instead of asking. The
 *    whole design rests on the operator being *asked* again every 30 minutes; a
 *    screen that showed "something went wrong" would train them to reload.
 * 3. A support view started without a reason — the thing the customer reads in
 *    their own audit log.
 * 4. Either proof opening the area, so neither field may be `required` on its
 *    own: the browser holds a submit back for an empty required field, and the
 *    operator sees a button that does nothing.
 * 5. The lapsed refusal reaching the route boundary instead of the screen —
 *    found by hand on 2026-09-21, after this suite had passed for a day against
 *    a query client that did not behave like the app's.
 */

const HTTP_FORBIDDEN = 403;

const ORG = "00000000-0000-0000-0000-0000000000a1";
const PRIYA = "00000000-0000-0000-0000-0000000000b2";

const ROW = {
  id: ORG,
  slug: "acme",
  name: "Acme",
  region: "india",
  plan: "pro",
  created_at: "2026-09-01T09:00:00Z",
  deleted: false,
  suspended: false,
};

const ORGANIZATION = {
  organization: {
    id: ORG,
    slug: "acme",
    name: "Acme",
    region: "india",
    plan: "pro",
    created_at: "2026-09-01T09:00:00Z",
    deleted: false,
    suspended: false,
  },
  suspension: null,
  plan_grant: null,
  members: [
    { id: PRIYA, email: "priya@acme.test", name: "Priya", role: "member", status: "active" },
    { id: "sam", email: "sam@acme.test", name: null, role: "viewer", status: "invited" },
  ],
  projects: [],
  usage: null,
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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

/** A refusal arrives as RFC 9457, which is the only shape `ApiError` keeps a body from. */
const problem = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/problem+json" },
  });

/** The API's own refusal when the 30 minutes have run out. */
const LAPSED = {
  type: "https://nexovue.com/problems/permission-denied",
  title: "This session may not perform that action",
  status: 403,
  detail:
    "Confirm it is you before administering the platform: this session has not been " +
    "re-verified in the last 30 minutes.",
};

function stubApi(
  role: TestRole | "super_admin",
  options: {
    platform?: unknown;
    status?: number;
    entered?: string[];
    list?: unknown;
    searched?: string[];
  } = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      if (url.pathname.endsWith("/auth/me")) {
        return json({
          user: {
            id: "u1",
            email: `${role}@example.test`,
            name: role,
            role,
            email_verified_at: null,
            permissions: role === "super_admin" ? [] : PERMISSIONS_OF[role as TestRole],
          },
          organization: { id: "o1", slug: "acme", name: "Acme" },
        });
      }
      if (url.pathname.endsWith("/auth/admin-mode")) {
        options.entered?.push(String(init?.body));
        return json({ until: "2026-09-20T12:00:00Z" });
      }
      if (url.pathname.endsWith("/admin/organizations")) {
        if (options.status === 403) return problem(LAPSED, 403);
        options.searched?.push(url.search);
        return json(options.list ?? { organizations: [ROW], truncated: false });
      }
      if (url.pathname.includes("/admin/organizations")) {
        if (options.status === 403) return problem(LAPSED, 403);
        return json(options.platform ?? ORGANIZATION);
      }
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

async function mount(screen: () => React.ReactElement, path = "/admin") {
  const container = document.createElement("div");
  document.body.append(container);
  // **The app's own 403 rule, restated here on purpose.** `app/query-client.ts`
  // throws every 403 to the route error boundary; a bare client does not, and a
  // bare client is what let the step-up panel pass this suite while the real
  // console replaced the whole screen with "this session may not perform that
  // action" and offered no way to confirm. A feature may not import `app/`, so
  // the rule is copied with its reason rather than skipped.
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        throwOnError: (error: unknown) =>
          error instanceof ApiError && error.status === HTTP_FORBIDDEN,
      },
      mutations: { retry: false },
    },
  });
  const rootRoute = createRootRoute();
  const route = createRoute({
    getParentRoute: () => rootRoute,
    path,
    component: () => createElement(QueryClientProvider, { client }, screen()),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([route]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return container;
}

/** React listens for `input` at the root, so a controlled field needs the native setter. */
function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("the platform area", () => {
  it("refuses a customer, whatever their role in their own organization", async () => {
    stubApi("owner");
    const container = await mount(() => createElement(PlatformScreen));

    expect(container.textContent).toContain("nexovue's own area");
    expect(container.querySelector("input")).toBeNull();
  });

  it("asks the operator to confirm again when the thirty minutes have run out", async () => {
    const entered: string[] = [];
    stubApi("super_admin", { status: 403, entered });
    const container = await mount(() => createElement(OrganizationScreen, { orgId: ORG }));

    const panel = container.querySelector<HTMLElement>('section[aria-label="Confirm it is you"]');
    expect(panel).not.toBeNull();
    // It asks — it does not report a failure the operator can only reload past.
    expect(container.textContent).not.toContain("could not be loaded");

    const password = panel?.querySelector<HTMLInputElement>('input[name="password"]');
    await act(async () => typeInto(password as HTMLInputElement, "a long operator password"));
    // `requestSubmit` runs the browser's own validation first: a field wrongly
    // marked required would silently hold the submit back, which is defect 4.
    await act(async () => panel?.querySelector("form")?.requestSubmit());
    await settle();

    expect(JSON.parse(entered[0] as string)).toEqual({ password: "a long operator password" });
  });

  it("opens as a list of customers, without being asked to search (founder, 2026-09-21)", async () => {
    // It used to open as a search box over an empty screen, which asks an
    // operator to recall a name before it will show them anything.
    const searched: string[] = [];
    stubApi("super_admin", { searched });
    const container = await mount(() => createElement(PlatformScreen));

    expect(container.querySelector('ul[aria-label="Organizations"]')).not.toBeNull();
    expect(container.textContent).toContain("Acme");
    expect(container.textContent).toContain("1 customer");
    // Asked for everything, not for a needle.
    expect(searched).toEqual([""]);
  });

  it("says when the list is the newest of more, rather than reading as all of them", async () => {
    stubApi("super_admin", { list: { organizations: [ROW], truncated: true } });
    const container = await mount(() => createElement(PlatformScreen));

    expect(container.textContent).toContain("Search to reach the rest");
  });

  it("shows the operator one organization, with the valve and the plan", async () => {
    stubApi("super_admin");
    const container = await mount(() => createElement(OrganizationScreen, { orgId: ORG }));

    expect(container.textContent).toContain("Acme");
    expect(container.querySelector('section[aria-label="Ingest"]')).not.toBeNull();
    expect(container.querySelector('section[aria-label="Plan"]')).not.toBeNull();
    // Only somebody who has actually signed in can be viewed as.
    const members = container.querySelector<HTMLElement>('section[aria-label="Members"]');
    const offered = [...(members?.querySelectorAll("button") ?? [])].map((b) => b.textContent);
    expect(offered).toEqual(["View as"]);
  });

  it("will not start a support view without a reason", async () => {
    stubApi("super_admin");
    const container = await mount(() => createElement(OrganizationScreen, { orgId: ORG }));
    const members = container.querySelector<HTMLElement>('section[aria-label="Members"]');

    await act(async () => members?.querySelector("button")?.click());
    const form = container.querySelector<HTMLFormElement>('form[aria-label="View as Priya"]');
    const submit = form?.querySelector<HTMLButtonElement>('button[type="submit"]');

    expect(submit?.disabled).toBe(true);
  });
});
