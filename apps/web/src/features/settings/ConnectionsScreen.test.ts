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
import type { TestRole } from "@/shared/identity.test-util";
import { identityFor } from "@/shared/identity.test-util";

import type { ConnectionsOverview, ConnectionView, SetupStep } from "./api";
import { ConnectionsBanner } from "./ConnectionsBanner";
import { ConnectionsScreen } from "./ConnectionsScreen";
import { GitHubReturnScreen } from "./GitHubReturnScreen";
import { GitHubStartScreen } from "./GitHubStartScreen";

/**
 * F22 T3.9, T3.10: `/settings/connections` in every state and setup row of `design.md`
 * (AC81, AC113, AC124), the full-page start that posts GitHub's manifest form, the return
 * GitHub redirects to, and the console-wide banner.
 */

const mounted: Root[] = [];
// What the start page asked to submit; jsdom does not submit forms.
const submitted: HTMLFormElement[] = [];
const CONNECTION = "5f6d6f14-0e3b-4a2f-8b5d-1f3f7d6a0c01";

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

const problem = (status: number, detail: string, reason: string) =>
  new Response(JSON.stringify({ type: "about:blank", title: "x", status, detail, reason }), {
    status,
    headers: { "content-type": "application/problem+json" },
  });

function view(overrides: Partial<ConnectionView> = {}): ConnectionView {
  return {
    id: CONNECTION,
    kind: "github",
    state: "working",
    app_slug: "nexovue-acme",
    install_url: "https://github.com/apps/nexovue-acme/installations/new",
    setup_step: null,
    setup_expires_at: null,
    last_ok_at: new Date(Date.now() - 3 * 60_000).toISOString(),
    failing_since: null,
    last_failure: null,
    last_failure_status: null,
    repositories: [
      { id: "r1", key: "acme/api", is_private: true, is_reachable: true },
      { id: "r2", key: "acme/site", is_private: false, is_reachable: true },
    ],
    ...overrides,
  };
}

function overview(
  connections: ConnectionView[],
  extra: Partial<ConnectionsOverview> = {},
): ConnectionsOverview {
  return {
    available: true,
    unavailable_reason: null,
    email_available: true,
    connections,
    ...extra,
  };
}

interface Sent {
  method: string;
  path: string;
  body: unknown;
}

function stubApi(
  role: TestRole,
  answer: ConnectionsOverview,
  extra: (path: string, method: string, body: unknown) => Response | undefined = () => undefined,
): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, init?: RequestInit) => {
      const path = url.pathname.replace("/api/v1/", "");
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      sent.push({ method, path, body });
      const special = extra(path, method, body);
      if (special) return special;
      if (path === "auth/me") return json(identityFor(role));
      if (path === "system/info") return json({ deployment: "self-hosted" });
      if (path === "connections") return json(answer);
      throw new Error(`no stub for ${method} ${url.pathname}`);
    }),
  );
  return sent;
}

async function settle() {
  for (let round = 0; round < 12; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(Screen: () => unknown, path: string, at = path) {
  const container = document.createElement("div");
  document.body.append(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rootRoute = createRootRoute();
  const screen = createRoute({
    getParentRoute: () => rootRoute,
    path,
    // biome-ignore lint/suspicious/noExplicitAny: each screen is a plain component.
    component: () => createElement(QueryClientProvider, { client }, createElement(Screen as any)),
  });
  const settings = createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings/connections",
    component: () => createElement("p", null, "settings landing"),
  });
  const routes = path === "/settings/connections" ? [screen] : [screen, settings];
  const router = createRouter({
    routeTree: rootRoute.addChildren(routes),
    history: createMemoryHistory({ initialEntries: [at] }),
  });
  await router.load();
  const root = createRoot(container);
  mounted.push(root);
  // biome-ignore lint/suspicious/noExplicitAny: the test router is not the app's registered one.
  await act(async () => root.render(createElement(RouterProvider, { router } as any)));
  await settle();
  return { container, router };
}

const texts = (element: Element, selector: string) =>
  [...element.querySelectorAll(selector)].map((each) => each.textContent?.trim() ?? "");

describe("where connections do not exist", () => {
  it("says exactly the hosted sentence, and nothing else (AC81)", async () => {
    stubApi("owner", overview([], { available: false, unavailable_reason: "hosted" }));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    const body = container.querySelector("[data-connections-body]");
    expect(body?.textContent?.trim()).toBe(
      "Tracker connections are not available on the hosted service yet.",
    );
    expect(texts(body ?? container, "button")).toEqual([]);
    expect(container.textContent).not.toContain("What leaves this server");
    // Not even the screen's own description, which speaks of GitHub issues.
    expect(container.textContent).not.toContain("GitHub issues");
  });

  it("says connections are turned off on this install (AC118)", async () => {
    stubApi("owner", overview([], { available: false, unavailable_reason: "off" }));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain(
      "Tracker connections are turned off on this install. An operator can turn them on: see the setup page.",
    );
    expect(texts(container, "a")).not.toContain("Connect GitHub");
  });
});

describe("with nothing connected", () => {
  it("says what connecting does and does not, and starts on a full page", async () => {
    stubApi("owner", overview([]));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain("What leaves this server");
    expect(container.textContent).toContain(
      "Status only. Nothing is created without someone's click.",
    );
    const start = [...container.querySelectorAll("a")].find(
      (anchor) => anchor.textContent === "Connect GitHub",
    );
    // A plain anchor, so the browser loads the page with its own form policy (AC73).
    expect(start?.getAttribute("href")).toBe("/settings/connections/github/start");
    expect(start?.hasAttribute("data-status")).toBe(false);
  });

  it("names no Jira while Atlassian has not answered (T0.1)", async () => {
    stubApi("owner", overview([]));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).not.toMatch(/jira/i);
  });
});

describe("each GitHub setup row says its words and offers its one action (AC124)", () => {
  const rows: [SetupStep, string, string][] = [
    ["on_github", "Setup did not finish on GitHub.", "Start again"],
    ["expired", "This setup link has expired. Nothing was changed.", "Start again"],
    ["refused", "GitHub did not accept this setup. Nothing was changed.", "Start again"],
    ["not_installed", "Now choose which repositories it may reach.", "Install on GitHub"],
    [
      "no_repository",
      "The App is installed, but reaches no repository.",
      "Choose repositories on GitHub",
    ],
    ["testing", "Testing the connection.", "Test again"],
  ];

  it.each(rows)("%s", async (step, words, action) => {
    stubApi("owner", overview([view({ state: "setting_up", setup_step: step, repositories: [] })]));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain(words);
    const actions = [...texts(container, "button"), ...texts(container, "a")].filter(
      (label) => label === action,
    );
    expect(actions).toEqual([action]);
    // Every setup can be stopped, setting the slot free.
    expect(texts(container, "button")).toContain("Stop setting up");
  });

  it("a first test that failed says why in words, with Test again", async () => {
    stubApi(
      "owner",
      overview([
        view({
          state: "setting_up",
          setup_step: "test_failed",
          last_failure: "unauthorized",
          repositories: [],
        }),
      ]),
    );
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain("GitHub refused the App's key.");
    expect(texts(container, "button")).toContain("Test again");
  });

  it("while the key is fetched there is nothing to press but stop", async () => {
    stubApi(
      "owner",
      overview([view({ state: "setting_up", setup_step: "converting", repositories: [] })]),
    );
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain(
      "GitHub sent the App back. nexovue is fetching its key.",
    );
    const body = container.querySelector("[data-connections-body]") ?? container;
    expect(texts(body, "button")).toEqual(["Stop setting up"]);
  });
});

describe("a connection that exists (FR31, AC113)", () => {
  it("working: the App, its reach, last synced, a test, a new key and disconnect", async () => {
    stubApi("owner", overview([view()]));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain("nexovue-acme");
    expect(container.textContent).toMatch(/Last synced 3 min/);
    expect(container.textContent).toContain("acme/api");
    expect(container.textContent).toContain("Private");
    expect(container.textContent).toContain("Public");
    for (const control of ["Test this connection", "Replace the App key", "Disconnect"]) {
      expect(texts(container, "button")).toContain(control);
    }
  });

  it("failing: since when, and that nexovue keeps trying", async () => {
    stubApi(
      "owner",
      overview([
        view({
          state: "failing",
          failing_since: "2026-11-08T10:00:00Z",
          last_failure: "server_error",
        }),
      ]),
    );
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toMatch(/Failing since .+\. nexovue keeps trying\./);
  });

  it("needs attention: what broke and exactly what to do", async () => {
    stubApi(
      "owner",
      overview([view({ state: "needs_attention", last_failure: "key_unreadable" })]),
    );
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain(
      "This install's CONNECTION_SECRET_KEY cannot open the stored key.",
    );
  });

  it("says when failure emails cannot be sent from this install", async () => {
    stubApi("owner", overview([view()], { email_available: false }));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain("Failure emails cannot be sent on this install.");
  });

  it("an unreachable repository says so in words, never colour alone", async () => {
    stubApi(
      "owner",
      overview([
        view({
          repositories: [{ id: "r1", key: "acme/api", is_private: true, is_reachable: false }],
        }),
      ]),
    );
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain("No longer reached");
  });

  it("a test's result arrives on the page without leaving it (FR30)", async () => {
    const sent = stubApi("owner", overview([view()]), (path, method) => {
      if (path === "connection-tests" && method === "POST")
        return json({ request_id: "req-1" }, 202);
      if (path === "connection-tests/req-1") {
        return json({ pending: false, ok: true, failure: null, state: "working" });
      }
      return undefined;
    });
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    const test = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Test this connection",
    );
    await act(async () => test?.click());
    await settle();

    expect(sent.find((each) => each.path === "connection-tests")?.body).toEqual({
      connection_id: CONNECTION,
    });
    expect(container.textContent).toContain("The connection works.");
  });

  it("disconnecting asks first, then says what stays behind", async () => {
    const sent = stubApi("owner", overview([view()]), (path, method) =>
      path === `connections/${CONNECTION}` && method === "DELETE"
        ? new Response(null, { status: 204 })
        : undefined,
    );
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    const open = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Disconnect",
    );
    await act(async () => open?.click());
    expect(container.textContent).toContain(
      "The App stays in your GitHub account until you delete it there. A backup may hold a sealed copy.",
    );
    const confirm = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Disconnect GitHub",
    );
    await act(async () => confirm?.click());
    await settle();

    expect(sent.some((each) => each.method === "DELETE")).toBe(true);
  });
});

describe("whose screen it is", () => {
  it.each(["member", "viewer"] as const)("a %s is told who can open it", async (role) => {
    stubApi(role, overview([view()]));
    const { container } = await mount(ConnectionsScreen, "/settings/connections");

    expect(container.textContent).toContain("an owner or an admin");
    expect(container.textContent).not.toContain("acme/api");
  });
});

function StartForTest() {
  return createElement(GitHubStartScreen, { submit: (form) => submitted.push(form) });
}

describe("the full-page start (T3.9, AC73)", () => {
  it("asks whether GitHub can reach this server, no by default, then posts the manifest", async () => {
    submitted.length = 0;
    const sent = stubApi("owner", overview([]), (path, method) =>
      path === "connections/github/manifest" && method === "POST"
        ? json(
            {
              connection_id: CONNECTION,
              state: "one-time-state",
              manifest: { name: "nexovue-acme", public: false },
              post_url: "https://github.com/settings/apps/new",
            },
            201,
          )
        : undefined,
    );
    const { container } = await mount(StartForTest, "/settings/connections/github/start");

    expect(container.textContent).toContain(
      "You will go to GitHub to create a private App named nexovue-acme. You may need a GitHub organization owner.",
    );
    expect(container.textContent).toContain("Can GitHub reach this server from the internet?");
    const no = container.querySelector<HTMLInputElement>("input[value='no']");
    expect(no?.checked).toBe(true);
    const go = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Continue to GitHub",
    );
    await act(async () => go?.click());
    await settle();

    expect(sent.find((each) => each.path === "connections/github/manifest")?.body).toEqual({
      reachable: false,
      github_organization: null,
    });
    const [form] = submitted;
    expect(form?.method).toBe("post");
    expect(form?.action).toBe("https://github.com/settings/apps/new?state=one-time-state");
    const field = form?.querySelector<HTMLInputElement>("input[name='manifest']");
    expect(JSON.parse(field?.value ?? "{}")).toEqual({ name: "nexovue-acme", public: false });
  });
});

describe("GitHub's return (design, step 3)", () => {
  it("posts the code and state, then lands on settings", async () => {
    const sent = stubApi("owner", overview([]), (path) =>
      path === "connections/github/manifest/complete"
        ? json({ connection_id: CONNECTION }, 202)
        : undefined,
    );
    const { container, router } = await mount(
      GitHubReturnScreen,
      "/settings/connections/github/return",
      "/settings/connections/github/return?code=c0de&state=s1",
    );

    expect(sent.find((each) => each.path === "connections/github/manifest/complete")?.body).toEqual(
      { code: "c0de", state: "s1" },
    );
    expect(router.state.location.pathname).toBe("/settings/connections");
    expect(container.textContent).toContain("settings landing");
  });

  it.each([
    ["invalid", "This setup link is not valid. Nothing was changed."],
    ["expired", "This setup link has expired. Nothing was changed."],
  ])("a %s setup link is refused in words, with Start again", async (reason, words) => {
    stubApi("owner", overview([]), (path) =>
      path === "connections/github/manifest/complete" ? problem(422, words, reason) : undefined,
    );
    const { container } = await mount(
      GitHubReturnScreen,
      "/settings/connections/github/return",
      "/settings/connections/github/return?code=c0de&state=s1",
    );

    expect(container.textContent).toContain(words);
    const again = [...container.querySelectorAll("a")].find(
      (anchor) => anchor.textContent === "Start again",
    );
    expect(again?.getAttribute("href")).toBe("/settings/connections");
  });
});

describe("the console-wide banner (FR31, AC113)", () => {
  it.each([
    ["owner", "needs_attention", true],
    ["admin", "needs_attention", true],
    ["member", "needs_attention", false],
    ["owner", "working", false],
  ] as const)("a %s, with GitHub %s: shown %s", async (role, state, shown) => {
    stubApi(role, overview([view({ state })]));
    const { container } = await mount(ConnectionsBanner, "/anywhere");

    expect(container.textContent?.includes("GitHub needs attention. Open settings.")).toBe(shown);
  });
});
